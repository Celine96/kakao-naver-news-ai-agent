import logging
import os
import asyncio
from datetime import datetime
from typing import Optional, Any
import uuid
from collections import deque
import re

from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI, OpenAIError, APITimeoutError
import numpy as np
import pickle

# 뉴스 크롤링용
import requests
from bs4 import BeautifulSoup

# Redis for queue management
try:
    import redis.asyncio as redis
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = Any
    logging.warning("redis package not installed. Using in-memory queue.")

# ================================================================================
# Logging Configuration
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="REXA - Real Estate Expert Assistant",
    description="Solar API + RAG chatbot for real estate + News QA",
    version="2.0.0"
)

# ================================================================================
# Configuration & Global Variables
# ================================================================================

# Naver News API
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Health Check Configuration
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", 5))
MAX_UNHEALTHY_COUNT = int(os.getenv("MAX_UNHEALTHY_COUNT", 3))

# Queue Configuration
WEBHOOK_QUEUE_NAME = "rexa:webhook_queue"
WEBHOOK_PROCESSING_QUEUE = "rexa:processing_queue"
WEBHOOK_FAILED_QUEUE = "rexa:failed_queue"
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", 3))
QUEUE_PROCESS_INTERVAL = int(os.getenv("QUEUE_PROCESS_INTERVAL", 5))

# API Timeout Configuration
API_TIMEOUT = int(os.getenv("API_TIMEOUT", 3))

# Global state
redis_client: Optional[Any] = None
server_healthy = True
unhealthy_count = 0
last_health_check = datetime.now()

# In-memory queue fallback
in_memory_webhook_queue: deque = deque()
in_memory_processing_queue: deque = deque()
in_memory_failed_queue: deque = deque()
use_in_memory_queue = False

# News session storage (user_id -> news_data)
news_sessions = {}

# ================================================================================
# Upstage Solar API Configuration
# ================================================================================

client = OpenAI(
    api_key=os.getenv("UPSTAGE_API_KEY"),
    base_url="https://api.upstage.ai/v1/solar",
    timeout=API_TIMEOUT
)

logger.info("✅ Upstage Solar API client configured")

# ================================================================================
# RAG - Load Embeddings
# ================================================================================

article_chunks = []
chunk_embeddings = []

try:
    with open("embeddings.pkl", "rb") as f:
        data = pickle.load(f)
        article_chunks = data["chunks"]
        chunk_embeddings = data["embeddings"]
    logger.info(f"✅ Loaded {len(article_chunks)} chunks from embeddings.pkl")
    logger.info(f"✅ RAG is ENABLED with {len(article_chunks)} chunks")
except FileNotFoundError:
    logger.warning("⚠️ embeddings.pkl not found - RAG will not be available")
    logger.warning("⚠️ Server will continue WITHOUT RAG - responses will be general")
    logger.warning("⚠️ To enable RAG: run 'python embedding2_solar.py' and redeploy")
except Exception as e:
    logger.error(f"❌ Failed to load embeddings: {e}")
    logger.warning("⚠️ Server will continue WITHOUT RAG")

# ================================================================================
# News Functions
# ================================================================================

def search_naver_news(query: str = "부동산", display: int = 1) -> Optional[dict]:
    """네이버 뉴스 API로 최신 뉴스 1개 검색"""
    url = "https://openapi.naver.com/v1/search/news.json"
    
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    
    params = {
        "query": query,
        "display": display,
        "sort": "date"  # 최신순
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        items = data.get('items', [])
        if not items:
            return None
            
        item = items[0]
        # HTML 태그 제거
        title = re.sub('<[^<]+?>', '', item['title'])
        description = re.sub('<[^<]+?>', '', item['description'])
        
        # HTML 엔티티 디코딩 (&quot; → ", &amp; → & 등)
        import html
        title = html.unescape(title)
        description = html.unescape(description)
        
        # 요약 길이 제한 (150자)
        if len(description) > 150:
            description = description[:150] + "..."
        
        return {
            "title": title,
            "description": description,
            "link": item['link'],  # 원본 URL 그대로
            "pubDate": item['pubDate']
        }
    except Exception as e:
        logger.error(f"❌ 뉴스 검색 오류: {e}")
        return None

def crawl_news_content(url: str) -> str:
    """뉴스 URL에서 본문 추출"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 네이버 뉴스 본문 추출
        if 'news.naver.com' in url:
            article = soup.select_one('#dic_area') or soup.select_one('#articeBody') or soup.select_one('.news_end')
            if article:
                # 불필요한 태그 제거
                for tag in article.find_all(['script', 'style', 'aside']):
                    tag.decompose()
                content = article.get_text(strip=True, separator='\n')
                return content[:2500]  # 최대 2500자 (Solar API 컨텍스트 고려)
        
        # 일반 뉴스 사이트 - p 태그 기반 추출
        paragraphs = soup.find_all('p')
        content = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
        return content[:2500] if content else "본문을 추출할 수 없습니다."
        
    except Exception as e:
        logger.error(f"❌ 크롤링 오류: {e}")
        return "본문을 가져올 수 없습니다."

# ================================================================================
# RAG Helper Functions
# ================================================================================

def cosine_similarity(a, b):
    """Calculate cosine similarity between two vectors"""
    from numpy import dot
    from numpy.linalg import norm
    return dot(a, b) / (norm(a) * norm(b))

async def get_relevant_context(prompt: str, top_n: int = 2) -> str:
    """Get relevant context from embeddings for RAG"""
    if not chunk_embeddings or not article_chunks:
        logger.warning("⚠️ No embeddings available for RAG")
        return ""
    
    try:
        # 임베딩 차원 자동 감지
        embedding_dim = len(chunk_embeddings[0])
        logger.info(f"📊 Detected embedding dimension: {embedding_dim}")
        
        # 차원에 따라 적절한 API 사용
        if embedding_dim == 1536:
            # OpenAI 임베딩 (text-embedding-3-small)
            logger.info("🔧 Using OpenAI embedding model")
            try:
                openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                q_embedding = openai_client.embeddings.create(
                    input=prompt, 
                    model="text-embedding-3-small"
                ).data[0].embedding
            except Exception as e:
                logger.error(f"❌ OpenAI embedding failed: {e}")
                logger.info("💡 Set OPENAI_API_KEY environment variable")
                return ""
                
        else:
            # Solar 임베딩 (모든 다른 차원)
            logger.info(f"🔧 Using Solar embedding model (dimension: {embedding_dim})")
            try:
                q_embedding = client.embeddings.create(
                    input=prompt, 
                    model="solar-embedding-1-large-query"  # Solar 쿼리용 모델
                ).data[0].embedding
            except Exception as e:
                logger.error(f"❌ Solar embedding failed: {e}")
                logger.error(f"   Model: solar-embedding-1-large-query")
                return ""
        
        # Calculate similarities
        similarities = [cosine_similarity(q_embedding, emb) for emb in chunk_embeddings]
        
        # Get top N most similar chunks
        top_indices = np.argsort(similarities)[-top_n:][::-1]
        selected_context = "\n\n".join([article_chunks[i] for i in top_indices])
        
        # Format similarities for logging
        similarity_scores = [f"{similarities[i]:.3f}" for i in top_indices]
        logger.info(f"✅ Retrieved {top_n} relevant chunks (similarities: {similarity_scores})")
        return selected_context
        
    except Exception as e:
        logger.error(f"❌ Error getting relevant context: {e}")
        return ""

# ================================================================================
# Pydantic Models
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
# Redis & Queue Management
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
            socket_connect_timeout=5,
            socket_keepalive=True,
            retry_on_timeout=True
        )
        await redis_client.ping()
        logger.info(f"✅ Redis connected: {REDIS_HOST}:{REDIS_PORT}")
        use_in_memory_queue = False
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed: {e}")
        logger.warning("⚠️ Using in-memory queue as fallback")
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
        logger.info(f"📥 Request {request_id[:8]} added to in-memory queue (size: {len(in_memory_webhook_queue)})")
        return
    
    if not redis_client:
        in_memory_webhook_queue.append(queued_req)
        logger.warning(f"⚠️ Redis unavailable - using in-memory queue")
        return
    
    try:
        await redis_client.lpush(WEBHOOK_QUEUE_NAME, queued_req.model_dump_json())
        queue_size = await redis_client.llen(WEBHOOK_QUEUE_NAME)
        logger.info(f"📥 Request {request_id[:8]} added to Redis queue (size: {queue_size})")
    except Exception as e:
        logger.error(f"❌ Failed to enqueue to Redis: {e}")
        in_memory_webhook_queue.append(queued_req)
        logger.info(f"📥 Fallback to in-memory queue (size: {len(in_memory_webhook_queue)})")

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
    except Exception as e:
        logger.error(f"❌ Failed to get queue sizes: {e}")
        return (0, 0, 0)

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
                logger.debug(f"✅ Health check passed at {last_health_check}")
                
            except Exception as e:
                unhealthy_count += 1
                logger.warning(f"⚠️ Health check failed ({unhealthy_count}/{MAX_UNHEALTHY_COUNT}): {e}")
                
                if unhealthy_count >= MAX_UNHEALTHY_COUNT:
                    server_healthy = False
                    logger.error(f"❌ Server marked as unhealthy after {unhealthy_count} failures")
                
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
                            logger.warning(f"⚠️ Retry {req.retry_count}/{MAX_RETRY_ATTEMPTS} for {req.request_id[:8]}")
                        else:
                            in_memory_failed_queue.append(req)
                            logger.error(f"❌ Request {req.request_id[:8]} moved to failed queue")
                continue
            
            if not redis_client:
                continue
            
            # Process from Redis queue
            req_json = await redis_client.rpoplpush(WEBHOOK_QUEUE_NAME, WEBHOOK_PROCESSING_QUEUE)
            
            if not req_json:
                continue
            
            req = QueuedRequest.model_validate_json(req_json)
            
            try:
                result = await process_solar_rag_request(req.request_body)
                await redis_client.lrem(WEBHOOK_PROCESSING_QUEUE, 1, req_json)
                logger.info(f"✅ Processed queued request {req.request_id[:8]}")
                
            except Exception as e:
                req.retry_count += 1
                req.error_message = str(e)
                
                await redis_client.lrem(WEBHOOK_PROCESSING_QUEUE, 1, req_json)
                
                if req.retry_count < MAX_RETRY_ATTEMPTS:
                    await redis_client.lpush(WEBHOOK_QUEUE_NAME, req.model_dump_json())
                    logger.warning(f"⚠️ Retry {req.retry_count}/{MAX_RETRY_ATTEMPTS} for {req.request_id[:8]}")
                else:
                    await redis_client.lpush(WEBHOOK_FAILED_QUEUE, req.model_dump_json())
                    logger.error(f"❌ Request {req.request_id[:8]} moved to failed queue")
                    
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
        
        # 사용자 ID 추출 (카카오톡 userRequest에서)
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
        
        # 컨텍스트 결정: 뉴스 세션이 있으면 뉴스, 없으면 RAG
        context = ""
        context_source = "general"
        
        # 뉴스 세션 확인
        if user_id in news_sessions:
            news_data = news_sessions[user_id]
            context = f"다음은 최신 부동산 뉴스입니다:\n\n제목: {news_data['title']}\n\n{news_data['content']}\n\n위 뉴스를 참고하여 사용자의 질문에 답변해주세요."
            context_source = "news"
            logger.info(f"📰 Using news context for user {user_id}")
        else:
            # RAG 컨텍스트 사용
            context = await get_relevant_context(user_message, top_n=2)
            if context:
                context = f"다음은 관련 정보입니다:\n\n{context}\n\n위 정보를 참고하여 답변해주세요."
                context_source = "rag"
                logger.info(f"📚 Using RAG context")
        
        # System prompt 구성
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
        
        logger.info(f"✅ Solar API response received (context: {context_source})")
        logger.info(f"📝 Response: {ai_response[:100]}...")
        
        # 뉴스 모드일 경우 Quick Reply 추가
        kakao_response = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": ai_response}}
                ]
            }
        }
        
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
        logger.error(f"❌ Solar API error: {type(e).__name__}: {e}")
        raise

# ================================================================================
# API Endpoints
# ================================================================================

@app.post("/generate")
async def generate(request: RequestBody):
    """REXA 부동산 전문 챗봇 with RAG - 카카오톡 5초 제한 대응"""
    request_id = str(uuid.uuid4())
    
    logger.info("="*50)
    logger.info(f"📨 New RAG request received: {request_id[:8]}")
    logger.info(f"📋 Full request body: {request.model_dump()}")
    
    try:
        # 3초 타임아웃으로 빠른 응답 시도
        result = await process_solar_rag_request(request.model_dump())
        logger.info(f"✅ Request {request_id[:8]} completed successfully")
        return result
        
    except APITimeoutError as e:
        logger.warning(f"⏰ Timeout (3s) - enqueueing request {request_id}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "답변 생성에 시간이 걸리고 있습니다. 잠시 후 다시 질문해주세요."
                        }
                    }
                ]
            }
        }
        
    except OpenAIError as e:
        logger.error(f"❌ API Error: {e}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                        }
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error: {type(e).__name__}: {e}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "죄송합니다. 오류가 발생했습니다. 다시 한번 질문해주시겠어요?"
                        }
                    }
                ]
            }
        }

@app.post("/news")
async def news_bot(request: RequestBody):
    """부동산 뉴스봇 - 뉴스 1개 불러오고 질의응답 세션 시작"""
    request_id = str(uuid.uuid4())
    
    logger.info("="*50)
    logger.info(f"📰 News bot request received: {request_id[:8]}")
    
    try:
        # 사용자 ID 추출
        request_dict = request.model_dump()
        user_request = request_dict.get("userRequest", {})
        user_info = user_request.get("user", {})
        user_id = user_info.get("id", "default")
        
        # 네이버 뉴스 검색
        news_item = search_naver_news("부동산", display=1)
        
        if not news_item:
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": "뉴스를 불러올 수 없습니다. 잠시 후 다시 시도해주세요."}}
                    ]
                }
            }
        
        # 뉴스 본문 크롤링 (질의응답용)
        news_content = crawl_news_content(news_item['link'])
        
        # 세션에 저장 (title, description, url, content)
        news_sessions[user_id] = {
            "title": news_item['title'],
            "description": news_item['description'],
            "content": news_content,
            "url": news_item['link'],
            "timestamp": datetime.now().isoformat()
        }
        
        logger.info(f"✅ News session created for user {user_id}")
        logger.info(f"📰 News: {news_item['title'][:50]}...")
        
        # 네이버 API 요약(description) 사용
        summary = news_item['description']
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"📰 {news_item['title']}\n\n{summary}\n\n🔗 {news_item['link']}\n\n💬 이 뉴스에 대해 궁금한 점을 물어보세요!"
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
                        "webLinkUrl": news_item['link']
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ News bot error: {type(e).__name__}: {e}")
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
    """REXA 부동산 전문 챗봇 with RAG - 카카오톡 5초 제한 대응"""
    request_id = str(uuid.uuid4())
    
    logger.info("="*50)
    logger.info(f"📨 New RAG request received: {request_id[:8]}")
    logger.info(f"📋 Full request body: {request.model_dump()}")
    
    try:
        # 3초 타임아웃으로 빠른 응답 시도
        result = await process_solar_rag_request(request.model_dump())
        logger.info(f"✅ Request {request_id[:8]} completed successfully")
        return result
        
    except APITimeoutError as e:
        logger.warning(f"⏰ Timeout (3s) - enqueueing request {request_id}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "답변 생성에 시간이 걸리고 있습니다. 잠시 후 다시 질문해주세요."
                        }
                    }
                ]
            }
        }
        
    except OpenAIError as e:
        logger.error(f"❌ API Error: {e}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                        }
                    }
                ]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error: {type(e).__name__}: {e}")
        await enqueue_webhook_request(request_id, request.model_dump())
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "죄송합니다. 오류가 발생했습니다. 다시 한번 질문해주시겠어요?"
                        }
                    }
                ]
            }
        }

@app.get("/health")
async def health_check() -> HealthStatus:
    """Enhanced health check endpoint"""
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
    """Simple ping endpoint for client health checks"""
    return {
        "alive": True,
        "healthy": server_healthy,
        "timestamp": datetime.now().isoformat(),
        "rag_enabled": len(chunk_embeddings) > 0,
        "news_sessions": len(news_sessions)
    }

@app.get("/queue/status")
async def queue_status():
    """Get detailed queue status"""
    queue_size, processing_size, failed_size = await get_queue_sizes()
    
    return {
        "queue_type": "in-memory" if use_in_memory_queue else "redis",
        "webhook_queue": queue_size,
        "processing_queue": processing_size,
        "failed_queue": failed_size,
        "total": queue_size + processing_size + failed_size,
        "rag_chunks_loaded": len(article_chunks),
        "active_news_sessions": len(news_sessions)
    }

@app.post("/queue/retry-failed")
async def retry_failed_requests():
    """Manually retry all failed requests"""
    try:
        if use_in_memory_queue:
            retry_count = len(in_memory_failed_queue)
            while len(in_memory_failed_queue) > 0:
                req = in_memory_failed_queue.pop()
                req.retry_count = 0
                in_memory_webhook_queue.appendleft(req)
            
            logger.info(f"✅ Retrying {retry_count} failed requests (in-memory)")
            return {"retried": retry_count, "queue_type": "in-memory"}
        
        if not redis_client:
            return {"error": "Queue not available"}
        
        failed_items = await redis_client.lrange(WEBHOOK_FAILED_QUEUE, 0, -1)
        retry_count = 0
        
        for item in failed_items:
            req = QueuedRequest.model_validate_json(item)
            req.retry_count = 0
            await redis_client.lpush(WEBHOOK_QUEUE_NAME, req.model_dump_json())
            retry_count += 1
        
        await redis_client.delete(WEBHOOK_FAILED_QUEUE)
        
        logger.info(f"✅ Retrying {retry_count} failed requests (Redis)")
        return {"retried": retry_count, "queue_type": "redis"}
        
    except Exception as e:
        logger.error(f"❌ Failed to retry requests: {e}")
        return {"error": str(e)}

# ================================================================================
# Startup & Shutdown Events
# ================================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    logger.info("="*70)
    logger.info("🚀 Starting REXA server (Solar + RAG + News)...")
    logger.info("="*70)
    
    # RAG 상태 확인
    if len(chunk_embeddings) > 0:
        logger.info(f"✅ RAG ENABLED: {len(chunk_embeddings)} chunks loaded")
    else:
        logger.warning("⚠️ RAG DISABLED: No embeddings loaded")
        logger.warning("⚠️ Server will work but without company-specific knowledge")
    
    # Naver API 확인
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
        logger.info("✅ Naver News API configured")
    else:
        logger.warning("⚠️ Naver News API not configured")
    
    # Redis 초기화
    await init_redis()
    
    # Background tasks
    asyncio.create_task(health_check_monitor())
    asyncio.create_task(queue_processor())
    
    logger.info("="*70)
    logger.info("✅ REXA server startup complete!")
    logger.info(f"   - Model: solar-mini")
    logger.info(f"   - RAG chunks: {len(chunk_embeddings)}")
    logger.info(f"   - Redis: {'connected' if redis_client else 'in-memory queue'}")
    logger.info(f"   - News API: {'enabled' if NAVER_CLIENT_ID else 'disabled'}")
    logger.info("="*70)

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    logger.info("👋 Shutting down REXA server (Solar + RAG + News)...")
    await close_redis()
    logger.info("✅ REXA server shut down successfully")
