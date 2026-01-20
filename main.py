"""
REXA 카카오톡 뉴스봇 서버 (v5.0.0 - Simple)
- 부동산 뉴스 제공
- 사용자 자동 등록
- 푸시 알림 준비
"""

import logging
import os
import asyncio
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

# 공통 함수 임포트
from common import (
    get_latest_news_from_gsheet,
    init_google_sheets,
    init_csv_file
)

# 사용자 관리 임포트
from user_management import register_or_update_user

# ================================================================================
# 로깅 설정
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="REXA - Real Estate News Bot",
    description="카카오톡 부동산 뉴스봇 + 푸시 알림",
    version="5.0.0"
)

# ================================================================================
# 글로벌 변수
# ================================================================================

server_healthy = True
last_health_check = datetime.now()

# ================================================================================
# Pydantic 모델
# ================================================================================

class DetailParams(BaseModel):
    prompt: dict

class Action(BaseModel):
    params: dict
    detailParams: dict

class UserInfo(BaseModel):
    id: str
    type: Optional[str] = None
    properties: Optional[dict] = None

class UserRequest(BaseModel):
    user: UserInfo

class RequestBody(BaseModel):
    action: Action
    userRequest: Optional[UserRequest] = None

class HealthStatus(BaseModel):
    status: str
    version: str
    server_healthy: bool
    last_check: str

# ================================================================================
# 사용자 등록 헬퍼
# ================================================================================

async def register_user_from_request(request_body: dict) -> Optional[str]:
    """
    요청에서 사용자 ID 추출 및 등록
    
    Returns:
        user_id 또는 None
    """
    try:
        user_request = request_body.get("userRequest", {})
        user_info = user_request.get("user", {})
        user_id = user_info.get("id")
        
        if user_id:
            # 백그라운드로 사용자 등록
            asyncio.create_task(
                asyncio.to_thread(
                    register_or_update_user,
                    user_id,
                    user_info.get("properties", {})
                )
            )
            return user_id
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 사용자 등록 오류: {e}")
        return None

# ================================================================================
# Background Tasks
# ================================================================================

async def health_check_monitor():
    """Monitor system health"""
    global server_healthy, last_health_check
    
    while True:
        try:
            await asyncio.sleep(60)  # 1분마다 체크
            
            # 간단한 헬스 체크
            server_healthy = True
            last_health_check = datetime.now()
            
        except Exception as e:
            logger.error(f"❌ Health check error: {e}")
            server_healthy = False

# ================================================================================
# API 엔드포인트
# ================================================================================

@app.post("/news")
async def news_bot(request: RequestBody):
    """부동산 뉴스봇 - 최신 뉴스 5개 제공"""
    request_id = str(uuid.uuid4())
    
    logger.info("=" * 50)
    logger.info(f"📰 News bot request: {request_id[:8]}")
    
    try:
        # 요청 데이터
        request_dict = request.model_dump()
        
        # 사용자 등록 (백그라운드)
        user_id = await register_user_from_request(request_dict)
        if user_id:
            logger.info(f"👤 사용자: {user_id[:10]}...")
        
        # 구글 시트에서 최신 뉴스 5개 조회
        news_items = get_latest_news_from_gsheet(limit=5)
        
        if not news_items or len(news_items) == 0:
            logger.warning("⚠️ 구글 시트에 뉴스 없음")
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": "최신 뉴스를 준비 중입니다. 잠시 후 다시 시도해주세요."}}
                    ]
                }
            }
        
        logger.info(f"✅ 구글 시트 조회 완료: {len(news_items)}개")
        
        # 로깅
        for idx, item in enumerate(news_items, 1):
            logger.info(
                f"   [{idx}] {item['title'][:40]}... "
                f"(점수: {item.get('relevance_score', 0)})"
            )
        
        # 뉴스 리스트 텍스트 생성
        news_list = f"📰 오늘의 부동산 뉴스 (총 {len(news_items)}건)\n\n"
        
        for idx, item in enumerate(news_items, 1):
            title = item.get('title', '제목 없음')
            url = item.get('link', '')
            
            # 디버깅: URL 확인
            logger.info(f"   뉴스 {idx}: URL = {url[:50] if url else 'URL 없음!'}")
            
            # URL이 없으면 경고
            if not url:
                logger.warning(f"   ⚠️ 뉴스 {idx} URL 없음: {title[:30]}")
                url = "(URL 정보 없음)"
            
            # 제목 + URL
            news_list += f"{idx}. {title}\n{url}\n\n"
        
        logger.info(f"✅ 응답 완료")
        
        # 카카오톡 응답
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": news_list.strip()
                        }
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ News bot error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "뉴스를 불러오는 중 오류가 발생했습니다."}}
                ]
            }
        }

@app.get("/health")
async def health_check() -> HealthStatus:
    """Health check endpoint"""
    return HealthStatus(
        status="healthy" if server_healthy else "unhealthy",
        version="5.0.0",
        server_healthy=server_healthy,
        last_check=last_health_check.isoformat()
    )

@app.get("/health/ping")
async def health_ping():
    """Simple ping endpoint"""
    return {
        "alive": True,
        "healthy": server_healthy,
        "timestamp": datetime.now().isoformat(),
        "version": "5.0.0"
    }

# ================================================================================
# Startup & Shutdown
# ================================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    logger.info("=" * 70)
    logger.info("🚀 Starting REXA News Bot Server v5.0.0")
    logger.info("=" * 70)
    
    # CSV/Sheets 초기화
    csv_success = init_csv_file()
    gsheet_success = init_google_sheets()
    
    if csv_success:
        logger.info("✅ CSV logging enabled")
    if gsheet_success:
        logger.info("✅ Google Sheets logging enabled")
    
    # 사용자 관리 시트 초기화
    from user_management import init_user_sheets
    user_sheet_success = init_user_sheets()
    if user_sheet_success:
        logger.info("✅ User management enabled")
    
    # Background tasks
    asyncio.create_task(health_check_monitor())
    
    logger.info("=" * 70)
    logger.info("✅ REXA News Bot Server started!")
    logger.info(f"   - Version: 5.0.0 (Simple + Push)")
    logger.info(f"   - Features: News + User Management")
    logger.info("=" * 70)

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources"""
    logger.info("👋 Shutting down...")
    logger.info("✅ Shutdown complete")
