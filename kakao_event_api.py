"""
카카오 Event API를 활용한 푸시 메시지 전송
"""

import logging
import os
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)

# ================================================================================
# 환경변수
# ================================================================================

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_EVENT_NAME = os.getenv("KAKAO_EVENT_NAME", "event02")  # 카카오 비즈니스에서 설정한 이벤트명

# ================================================================================
# 카카오 Event API
# ================================================================================

def send_event_to_user(user_id: str, event_name: str = None, params: Dict = None) -> bool:
    """
    카카오 Event API를 통해 특정 사용자에게 이벤트 전송
    
    Args:
        user_id: 카카오 사용자 ID
        event_name: 이벤트명 (None이면 환경변수 사용)
        params: 이벤트 파라미터 (예: {"sys_city": "서울"})
    
    Returns:
        성공 여부
    """
    
    if not KAKAO_REST_API_KEY:
        logger.error("❌ KAKAO_REST_API_KEY 환경변수 없음")
        return False
    
    event_name = event_name or KAKAO_EVENT_NAME
    params = params or {}
    
    url = "https://kapi.kakao.com/v1/api/talk/bot/event/send"
    
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "event": event_name,
        "user": user_id
    }
    
    # 파라미터 추가
    if params:
        payload["params"] = params
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ Event 전송 성공: {user_id[:10]}... ({event_name})")
            return True
        else:
            logger.error(
                f"❌ Event 전송 실패 ({response.status_code}): "
                f"{response.text[:100]}"
            )
            return False
            
    except Exception as e:
        logger.error(f"❌ Event API 호출 오류: {e}")
        return False

def send_event_to_users(user_ids: List[str], event_name: str = None, params: Dict = None) -> Dict:
    """
    여러 사용자에게 이벤트 전송 (순차 처리)
    
    Args:
        user_ids: 사용자 ID 리스트
        event_name: 이벤트명
        params: 이벤트 파라미터
    
    Returns:
        {
            'success': 성공한 사용자 수,
            'failed': 실패한 사용자 수,
            'total': 전체 사용자 수
        }
    """
    
    logger.info("=" * 70)
    logger.info(f"📤 Event 대량 전송 시작: {len(user_ids)}명")
    logger.info("=" * 70)
    
    success_count = 0
    failed_count = 0
    
    for idx, user_id in enumerate(user_ids, 1):
        logger.info(f"   [{idx}/{len(user_ids)}] 전송 중: {user_id[:10]}...")
        
        # Event 전송
        success = send_event_to_user(user_id, event_name, params)
        
        if success:
            success_count += 1
        else:
            failed_count += 1
        
        # Rate Limit 방지 (초당 10건)
        if idx < len(user_ids):
            import time
            time.sleep(0.1)
    
    logger.info("=" * 70)
    logger.info(f"🎉 Event 대량 전송 완료!")
    logger.info(f"   - 성공: {success_count}명")
    logger.info(f"   - 실패: {failed_count}명")
    logger.info("=" * 70)
    
    return {
        'success': success_count,
        'failed': failed_count,
        'total': len(user_ids)
    }

# ================================================================================
# 뉴스 푸시 전송
# ================================================================================

async def send_daily_news_push(user_ids: List[str]) -> Dict:
    """
    매일 오전 8시 뉴스 푸시 전송
    
    Args:
        user_ids: 푸시 받을 사용자 ID 리스트
    
    Returns:
        전송 결과
    """
    
    logger.info("=" * 70)
    logger.info("🌅 오전 8시 뉴스 푸시 시작")
    logger.info("=" * 70)
    
    if not user_ids:
        logger.warning("⚠️ 등록된 사용자 없음")
        return {'success': 0, 'failed': 0, 'total': 0}
    
    # Event API로 푸시 전송
    result = send_event_to_users(
        user_ids=user_ids,
        event_name=KAKAO_EVENT_NAME,
        params={}  # 필요시 파라미터 추가 (예: {"sys_city": "서울"})
    )
    
    logger.info("🎉 오전 8시 뉴스 푸시 완료!")
    
    return result
