"""
REXA 카카오톡 봇 서버
- 사용자 요청 시 뉴스 전달
- RAG 기반 질의응답
"""

import logging
import os
import asyncio
import uuid
from datetime import datetime
from typing import Optional, Any
from collections import deque

from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI, OpenAIError, APITimeoutError
import numpy as np
import pickle

# 공통 함수 임포트
from common import (
    search_naver_news,
    crawl_news_content,
    save_all_news_background,
    init_google_sheets,
    init_csv_file
)

# Redis for queue management (optional)
try:
    import redis.asyncio as redis
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = Any
    logging.warning("redis package not installed. Using in-memory queue.")

# ================================================================================
# 로깅 설정
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="REXA - Real Estate Expert Assistant",
    description="카카오톡 부동산 뉴스봇 + RAG",
    version="3.0.0"
)

# ================================================================================
# 환경변수 & 설정
# ================================================================================

# Upstage Solar API
UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY")
API_TIMEOUT = int(os.getenv("API_TIMEOUT", 3))

# Redis (optional)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Queue
WEBHOOK_QUEUE_NAME = "rexa:webhook_queue"
WEBHOOK_PROCESSING_QUEUE = "rexa:processing_queue"
WEBHOOK_FAILED_QUEUE = "rexa:failed_queue"
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", 3))
QUEUE_PROCESS_INTERVAL = int(os.getenv("QUEUE_PROCESS_INTERVAL", 5))

# Health Check
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", 5))
MAX_UNHEALTHY_COUNT = int(os.getenv("MAX_UNHEALTHY_COUNT", 3))

# ================================================================================
# 글로벌 변수
# ================================================================================

# Solar API client
client = OpenAI(
    api_key=UPSTAGE_API_KEY,
    base_url="https://api.upstage.ai/v1/solar",
    timeout=API_TIMEOUT
)

# Redis
redis_client: Optional[Any] = None
use_in_memory_queue = False

# In-memory queue fallback
in_memory_webhook_queue: deque = deque()
in_memory_processing_queue: deque = deque()
in_memory_failed_queue: deque = deque()

# Health
server_healthy = True
unhealthy_count = 0
last_health_check = datetime.now()

# News sessions (user_id -> news_data)
news_sessions = {}

# RAG embeddings
article_chunks = []
chunk_embeddings = []

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

class QueuedRequest(BaseModel):
    request_id: str
    request_body: dict
    timestamp: str
    retry_count: int = 0
    error_message: Optional[str] = None

class HealthStatus(BaseModel):
    status: str
    model: str
    mode: str
    server_healthy: bool
    last_check: str
    redis_connected: bool
    queue_size: int
    processing_queue_size: int
    failed_queue_size: int

# ================================================================================
# RAG 임베딩 로드
# ================================================================================

try:
    with open("embeddings.pkl", "rb") as f:
        data = pickle.load(f)
        article_chunks = data["chunks"]
        chunk_embeddings = data["embeddings"]
    logger.info(f"✅ Loaded {len(article_chunks)} chunks from embeddings.pkl")
except FileNotFoundError:
    logger.warning("⚠️ embeddings.pkl not found - RAG disabled")
except Exception as e:
    logger.error(f"❌ Failed to load embeddings: {e}")

# ================================================================================
# RAG Helper Functions
# ================================================================================

def cosine_similarity(a, b):
    """Calculate cosine similarity"""
    from numpy import dot
    from numpy.linalg import norm
    return dot(a, b) / (norm(a) * norm(b))

async def get_relevant_context(prompt: str, top_n: int = 2) -> str:
    """Get relevant context from embeddings for RAG"""
    if not chunk_embeddings or not article_chunks:
        return ""
    
    try:
        embedding_dim = len(chunk_embeddings[0])
        
        # Solar embedding
        q_embedding = client.embeddings.create(
            input=prompt,
            model="solar-embedding-1-large-query"
        ).data[0].embedding
        
        # Calculate similarities
        similarities = [cosine_similarity(q_embedding, emb) for emb in chunk_embeddings]
        top_indices = np.argsort(similarities)[-top_n:][::-1]
        selected_context = "\n\n".join([article_chunks[i] for i in top_indices])
        
        return selected_context
    except Exception as e:
        logger.error(f"❌ Error getting relevant context: {e}")
        return ""

# ================================================================================
# Redis & Queue 관리
# ================================================================================

async def init_redis():
    """Initialize Redis connection"""
    global redis_client, use_in_memory_queue
    
    if not REDIS_AVAILABLE:
        logger.warning("⚠️ Redis package not installed - using in-memory queue")
        use_in_memory_queue = True
        return
    
    try:
        redis_client = await redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=5
        )
        await redis_client.ping()
        logger.info(f"✅ Redis connected: {REDIS_HOST}:{REDIS_PORT}")
        use_in_memory_queue = False
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")
        use_in_memory_queue = True

async def close_redis():
    """Close Redis connection"""
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("✅ Redis connection closed")

async def enqueue_webhook_request(request_id: str, request_body: dict):
    """Add webhook request to queue"""
    queued_req = QueuedRequest(
        request_id=request_id,
        request_body=request_body,
        timestamp=datetime.now().isoformat()
    )
    
    if use_in_memory_queue:
        in_memory_webhook_queue.append(queued_req)
        logger.info(f"📥 Request queued (in-memory): {len(in_memory_webhook_queue)}")
        return
    
    if not redis_client:
        in_memory_webhook_queue.append(queued_req)
        return
    
    try:
        await redis_client.lpush(WEBHOOK_QUEUE_NAME, queued_req.model_dump_json())
        queue_size = await redis_client.llen(WEBHOOK_QUEUE_NAME)
        logger.info(f"📥 Request queued (Redis): {queue_size}")
    except Exception as e:
        logger.error(f"❌ Failed to enqueue: {e}")
        in_memory_webhook_queue.append(queued_req)

async def get_queue_sizes():
    """Get sizes of all queues"""
    if use_in_memory_queue:
        return (
            len(in_memory_webhook_queue),
            len(in_memory_processing_queue),
            len(in_memory_failed_queue)
        )
    
    if not redis_client:
        return (0, 0, 0)
    
    try:
        webhook_size = await redis_client.llen(WEBHOOK_QUEUE_NAME)
        processing_size = await redis_client.llen(WEBHOOK_PROCESSING_QUEUE)
        failed_size = await redis_client.llen(WEBHOOK_FAILED_QUEUE)
        return (webhook_size, processing_size, failed_size)
    except:
        return (0, 0, 0)

# ================================================================================
# Background Tasks
# ================================================================================

async def health_check_monitor():
    """Background task to monitor server health"""
    global server_healthy, unhealthy_count, last_health_check
    
    while True:
        try:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            
            # Check Solar API
            try:
                test_response = client.chat.completions.create(
                    model="solar-mini",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=10,
                    timeout=2
                )
                
                server_healthy = True
                unhealthy_count = 0
                last_health_check = datetime.now()
                
            except Exception as e:
                unhealthy_count += 1
                logger.warning(f"⚠️ Health check failed ({unhealthy_count}/{MAX_UNHEALTHY_COUNT})")
                
                if unhealthy_count >= MAX_UNHEALTHY_COUNT:
                    server_healthy = False
                    logger.error(f"❌ Server marked as unhealthy")
                
        except Exception as e:
            logger.error(f"❌ Health check monitor error: {e}")

async def queue_processor():
    """Background task to process queued requests"""
    while True:
        try:
            await asyncio.sleep(QUEUE_PROCESS_INTERVAL)
            
            if use_in_memory_queue:
                while len(in_memory_webhook_queue) > 0:
                    req = in_memory_webhook_queue.popleft()
                    
                    try:
                        result = await process_solar_rag_request(req.request_body)
                        logger.info(f"✅ Processed queued request {req.request_id[:8]}")
                    except Exception as e:
                        req.retry_count += 1
                        req.error_message = str(e)
                        
                        if req.retry_count < MAX_RETRY_ATTEMPTS:
                            in_memory_webhook_queue.append(req)
                        else:
                            in_memory_failed_queue.append(req)
                            logger.error(f"❌ Request {req.request_id[:8]} failed")
                continue
            
            # Redis queue processing (생략 가능)
            
        except Exception as e:
            logger.error(f"❌ Queue processor error: {e}")

# ================================================================================
# Core Processing Functions
# ================================================================================

async def process_solar_rag_request(request_body: dict) -> dict:
    """Process request with Solar API + RAG or News context"""
    try:
        action = request_body.get("action", {})
        detail_params = action.get("detailParams", {})
        user_message_data = detail_params.get("prompt", {})
        user_message = user_message_data.get("value", "").strip()
        
        # 사용자 ID 추출
        user_request = request_body.get("userRequest", {})
        user_info = user_request.get("user", {})
        user_id = user_info.get("id", "default")
        
        if not user_message:
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": "질문을 입력해주세요."}}
                    ]
                }
            }
        
        logger.info(f"💬 User message: {user_message}")
        
        # 컨텍스트 결정
        context = ""
        context_source = "general"
        
        # 뉴스 세션 확인
        if user_id in news_sessions:
            news_data = news_sessions[user_id]
            context = f"다음은 최신 부동산 뉴스입니다:\n\n제목: {news_data['title']}\n\n{news_data['content']}\n\n위 뉴스를 참고하여 답변해주세요."
            context_source = "news"
            logger.info(f"📰 Using news context")
        else:
            # RAG 컨텍스트 사용
            context = await get_relevant_context(user_message, top_n=2)
            if context:
                context = f"다음은 관련 정보입니다:\n\n{context}\n\n위 정보를 참고하여 답변해주세요."
                context_source = "rag"
                logger.info(f"📚 Using RAG context")
        
        # System prompt
        system_prompt = "당신은 부동산 전문 AI 어시스턴트 REXA입니다."
        if context:
            system_prompt += f"\n\n{context}"
        
        # Solar API 호출
        response = client.chat.completions.create(
            model="solar-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            timeout=API_TIMEOUT
        )
        
        ai_response = response.choices[0].message.content.strip()
        logger.info(f"✅ Solar API response (context: {context_source})")
        
        # 카카오톡 응답
        kakao_response = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": ai_response}}
                ]
            }
        }
        
        # 뉴스 모드일 경우 Quick Reply 추가
        if context_source == "news":
            news_data = news_sessions[user_id]
            kakao_response["template"]["quickReplies"] = [
                {
                    "label": "뉴스 원문 보기",
                    "action": "webLink",
                    "webLinkUrl": news_data['url']
                },
                {
                    "label": "다른 질문하기",
                    "action": "message",
                    "messageText": "이 뉴스에서 핵심은 뭐야?"
                }
            ]
        
        return kakao_response
        
    except Exception as e:
        logger.error(f"❌ Solar API error: {e}")
        raise

# ================================================================================
# API 엔드포인트
# ================================================================================

@app.post("/news")
async def news_bot(request: RequestBody):
    """부동산 뉴스봇 - 사용자 요청 시"""
    request_id = str(uuid.uuid4())
    
    logger.info("=" * 50)
    logger.info(f"📰 News bot request: {request_id[:8]}")
    
    try:
        # 사용자 ID 추출
        request_dict = request.model_dump()
        user_request = request_dict.get("userRequest", {})
        user_info = user_request.get("user", {})
        user_id = user_info.get("id", "default")
        
        # 네이버 뉴스 검색 (20개)
        news_items = search_naver_news("부동산", display=20)
        
        if not news_items or len(news_items) == 0:
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": "뉴스를 불러올 수 없습니다. 잠시 후 다시 시도해주세요."}}
                    ]
                }
            }
        
        logger.info(f"📊 필터링 완료: 총 {len(news_items)}개 부동산 관련 뉴스")
        
        # 상위 3개 로깅
        for idx, item in enumerate(news_items[:3]):
            logger.info(
                f"   [{idx+1}] {item['title'][:40]}... "
                f"(점수: {item.get('relevance_score', 0)}, "
                f"지역: {item.get('region', 'N/A')})"
            )
        
        # 첫 번째 뉴스 크롤링
        first_news = news_items[0]
        
        # Rate Limit 방지 재시도 로직
        first_news_content = ""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                first_news_content = crawl_news_content(first_news['link'])
                if first_news_content and "본문을 가져올 수 없습니다" not in first_news_content:
                    break
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ 크롤링 재시도 {attempt + 1}/{max_retries}")
                    await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"❌ 크롤링 시도 {attempt + 1} 실패: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                else:
                    first_news_content = first_news['description']
        
        # 세션에 저장
        news_sessions[user_id] = {
            "title": first_news['title'],
            "description": first_news['description'],
            "content": first_news_content,
            "url": first_news['link'],
            "timestamp": datetime.now().isoformat()
        }
        
        # Solar AI 요약 생성
        try:
            summary_prompt = f"다음 뉴스를 3-4개의 완전한 문장으로 요약해주세요.\n\n제목: {first_news['title']}\n\n본문: {first_news_content[:1500]}"
            
            response = client.chat.completions.create(
                model="solar-mini",
                messages=[
                    {"role": "system", "content": "당신은 부동산 뉴스 전문 요약 AI입니다."},
                    {"role": "user", "content": summary_prompt}
                ],
                max_tokens=300,
                timeout=API_TIMEOUT
            )
            
            summary = response.choices[0].message.content.strip()
            logger.info(f"✅ Solar AI summary generated")
            
        except Exception as e:
            logger.error(f"❌ Summary generation failed: {e}")
            summary = first_news['description']
            last_period = summary.rfind('.')
            if last_period > 0:
                summary = summary[:last_period + 1]
        
        # 백그라운드 저장
        asyncio.create_task(save_all_news_background(news_items, user_id))
        
        # 카카오톡 응답
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"📰 오늘의 부동산 뉴스 (총 {len(news_items)}건)\n\n【{first_news['title']}】\n\n{summary}\n\n💬 이 뉴스에 대해 궁금한 점을 물어보세요!"
                        }
                    }
                ],
                "quickReplies": [
                    {
                        "label": "핵심 내용은?",
                        "action": "message",
                        "messageText": "이 뉴스의 핵심은 뭐야?"
                    },
                    {
                        "label": "시장 영향은?",
                        "action": "message",
                        "messageText": "이게 부동산 시장에 어떤 영향을 줄까?"
                    },
                    {
                        "label": "원문 보기",
                        "action": "webLink",
                        "webLinkUrl": first_news['link']
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ News bot error: {e}")
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "뉴스를 처리하는 중 오류가 발생했습니다. 다시 시도해주세요."}}
                ]
            }
        }

@app.post("/custom")
async def generate_custom(request: RequestBody):
    """REXA 챗봇 (RAG) - 카카오톡 5초 제한 대응"""
    request_id = str(uuid.uuid4())
    
    logger.info("=" * 50)
    logger.info(f"📨 RAG request: {request_id[:8]}")
    
    try:
        result = await process_solar_rag_request(request.model_dump())
        logger.info(f"✅ Request {request_id[:8]} completed")
        return result
        
    except APITimeoutError:
        logger.warning(f"⏰ Timeout - enqueueing {request_id}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "답변 생성에 시간이 걸리고 있습니다. 잠시 후 다시 질문해주세요."}}
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "오류가 발생했습니다. 다시 한번 질문해주세요."}}
                ]
            }
        }

@app.get("/health")
async def health_check() -> HealthStatus:
    """Health check endpoint"""
    queue_size, processing_size, failed_size = await get_queue_sizes()
    
    return HealthStatus(
        status="healthy" if server_healthy else "unhealthy",
        model="solar-mini",
        mode="rexa_chatbot_rag_news",
        server_healthy=server_healthy,
        last_check=last_health_check.isoformat(),
        redis_connected=(redis_client is not None and not use_in_memory_queue),
        queue_size=queue_size,
        processing_queue_size=processing_size,
        failed_queue_size=failed_size
    )

@app.get("/health/ping")
async def health_ping():
    """Simple ping endpoint"""
    return {
        "alive": True,
        "healthy": server_healthy,
        "timestamp": datetime.now().isoformat(),
        "rag_enabled": len(chunk_embeddings) > 0,
        "news_sessions": len(news_sessions)
    }

# ================================================================================
# Startup & Shutdown
# ================================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    logger.info("=" * 70)
    logger.info("🚀 Starting REXA Bot Server...")
    logger.info("=" * 70)
    
    # RAG 상태
    if len(chunk_embeddings) > 0:
        logger.info(f"✅ RAG ENABLED: {len(chunk_embeddings)} chunks")
    else:
        logger.warning("⚠️ RAG DISABLED: No embeddings loaded")
    
    # CSV/Sheets 초기화
    csv_success = init_csv_file()
    gsheet_success = init_google_sheets()
    
    if csv_success:
        logger.info("✅ CSV logging enabled")
    if gsheet_success:
        logger.info("✅ Google Sheets logging enabled")
    
    # Redis 초기화
    await init_redis()
    
    # Background tasks
    asyncio.create_task(health_check_monitor())
    asyncio.create_task(queue_processor())
    
    logger.info("=" * 70)
    logger.info("✅ REXA Bot Server started!")
    logger.info(f"   - Model: solar-mini")
    logger.info(f"   - RAG chunks: {len(chunk_embeddings)}")
    logger.info(f"   - Redis: {'connected' if redis_client else 'in-memory'}")
    logger.info("=" * 70)

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources"""
    logger.info("👋 Shutting down REXA Bot Server...")
    await close_redis()
    logger.info("✅ Shutdown complete")
