"""
REXA 카카오톡 뉴스봇 서버 (v5.3.0 - Simplified)
- 부동산 뉴스 제공 (사용자 발화시)
- Push 기능 제거
- 사용자 관리 기능 제거
"""

import logging
import uuid
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel

# 공통 함수 임포트
from common import (
    get_latest_news_from_gsheet,
    init_google_sheets,
    init_csv_file
)

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
    description="카카오톡 부동산 뉴스봇 (간소화 버전)",
    version="5.3.0"
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

class RequestBody(BaseModel):
    action: Action

class HealthStatus(BaseModel):
    status: str
    version: str
    server_healthy: bool
    last_check: str

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
        
        # ListCard 형식으로 뉴스 아이템 생성
        list_items = []
        for idx, item in enumerate(news_items, 1):
            title = item.get('title', '제목 없음')
            description = item.get('description', '')
            url = item.get('link', '')
            
            if not url:
                logger.warning(f"   ⚠️ 뉴스 {idx} URL 없음: {title[:30]}")
                url = "https://news.naver.com"  # 기본 URL
            
            # 설명이 너무 길면 잘라내기
            if len(description) > 80:
                description = description[:77] + "..."
            
            list_items.append({
                "title": title,
                "description": description,
                "link": {
                    "web": url
                }
            })
        
        logger.info(f"✅ 응답 완료")
        
        # 카카오톡 ListCard 응답
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "listCard": {
                            "header": {
                                "title": f"📰 실시간 부동산 뉴스 ({len(news_items)}건)"
                            },
                            "items": list_items,
                            "buttons": [
                                {
                                    "label": "전체보기",
                                    "action": "webLink",
                                    "webLinkUrl": "https://news.naver.com/main/list.naver?mode=LS2D&mid=shm&sid1=101&sid2=260"
                                }
                            ]
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

@app.post("/custom")
async def custom_response(request: RequestBody):
    """사용자 자유 텍스트 입력 응답"""
    request_id = str(uuid.uuid4())
    
    logger.info("=" * 50)
    logger.info(f"💬 Custom request: {request_id[:8]}")
    
    try:
        # 간단한 응답
        response_text = """안녕하세요! REXA 부동산 뉴스봇입니다. 🏠

📰 최신 부동산 뉴스를 확인하시려면 아래 '부동산 뉴스' 버튼을 눌러주세요."""
        
        logger.info(f"✅ 응답 완료")
        
        # 카카오톡 응답
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": response_text
                        }
                    }
                ],
                "quickReplies": [
                    {
                        "label": "부동산 뉴스",
                        "action": "block",
                        "blockId": "YOUR_NEWS_BLOCK_ID"
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Custom response error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "안녕하세요! '부동산 뉴스' 버튼을 눌러주세요."}}
                ]
            }
        }

@app.get("/health")
async def health_check() -> HealthStatus:
    """Health check endpoint"""
    return HealthStatus(
        status="healthy" if server_healthy else "unhealthy",
        version="5.3.0",
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
        "version": "5.3.0"
    }

# ================================================================================
# Startup & Shutdown
# ================================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    global server_healthy, last_health_check
    
    logger.info("=" * 70)
    logger.info("🚀 Starting REXA News Bot Server v5.3.0 (Simplified)")
    logger.info("=" * 70)
    
    # CSV/Sheets 초기화
    csv_success = init_csv_file()
    gsheet_success = init_google_sheets()
    
    if csv_success:
        logger.info("✅ CSV logging enabled")
    if gsheet_success:
        logger.info("✅ Google Sheets logging enabled")
    
    server_healthy = True
    last_health_check = datetime.now()
    
    logger.info("=" * 70)
    logger.info("✅ REXA News Bot Server started!")
    logger.info(f"   - Version: 5.3.0 (Simplified)")
    logger.info(f"   - Features: News Only (No Push, No User Management)")
    logger.info("=" * 70)

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources"""
    logger.info("👋 Shutting down...")
    logger.info("✅ Shutdown complete")
