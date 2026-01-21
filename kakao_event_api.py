"""
카카오 Event API (올바른 버전) - Bot ID 기반
"""

import os
import requests
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# ================================================================================
# 환경변수
# ================================================================================

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_BOT_ID = os.getenv("KAKAO_BOT_ID")  # 카카오 비즈니스에서 확인
KAKAO_EVENT_NAME = os.getenv("KAKAO_EVENT_NAME", "event02")

# ================================================================================
# 올바른 Event API
# ================================================================================

def send_event_to_user(user_id: str, user_type: str = "botUserKey") -> bool:
    """
    카카오 Event API를 통해 특정 사용자에게 이벤트 전송
    
    Args:
        user_id: 사용자 ID
        user_type: "botUserKey" 또는 "plusfriendUserKey" 또는 "appUserId"
    
    Returns:
        성공 여부
    """
    
    if not KAKAO_REST_API_KEY:
        logger.error("❌ KAKAO_REST_API_KEY 환경변수 없음")
        return False
    
    if not KAKAO_BOT_ID:
        logger.error("❌ KAKAO_BOT_ID 환경변수 없음")
        logger.error("   → 카카오 비즈니스 > 챗봇 관리 > 설정에서 Bot ID 확인")
        return False
    
    # 올바른 엔드포인트
    url = f"https://bot-api.kakao.com/v2/bots/{KAKAO_BOT_ID}/talk"
    
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "event": {
            "name": KAKAO_EVENT_NAME
        },
        "user": [
            {
                "type": user_type,
                "id": user_id
            }
        ]
    }
    
    logger.info(f"🔗 API URL: {url}")
    logger.info(f"📝 Event: {KAKAO_EVENT_NAME}")
    logger.info(f"👤 User: {user_type} - {user_id[:10]}...")
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        logger.info(f"📊 응답 상태: {response.status_code}")
        logger.info(f"📊 응답 본문: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            
            if result.get("status") == "SUCCESS":
                task_id = result.get("taskId")
                logger.info(f"✅ Event 전송 성공!")
                logger.info(f"   → Task ID: {task_id}")
                logger.info(f"   → User: {user_id[:10]}...")
                return True
            else:
                logger.error(f"❌ Event 전송 실패")
                logger.error(f"   → Status: {result.get('status')}")
                logger.error(f"   → Message: {result.get('message')}")
                return False
        else:
            logger.error(f"❌ HTTP 에러: {response.status_code}")
            logger.error(f"   → {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Event API 호출 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def send_event_to_users(user_ids: List[str], user_type: str = "botUserKey") -> Dict:
    """
    여러 사용자에게 이벤트 전송 (최대 100명)
    
    Args:
        user_ids: 사용자 ID 리스트 (최대 100개)
        user_type: "botUserKey" 또는 "plusfriendUserKey" 또는 "appUserId"
    
    Returns:
        전송 결과
    """
    
    if not KAKAO_REST_API_KEY or not KAKAO_BOT_ID:
        logger.error("❌ 환경변수 없음")
        return {'success': 0, 'failed': len(user_ids), 'total': len(user_ids)}
    
    # API는 최대 100명까지 한 번에 전송 가능
    if len(user_ids) > 100:
        logger.warning(f"⚠️ 사용자 수 ({len(user_ids)}명) > 100명 제한")
        logger.warning("   → 처음 100명만 전송")
        user_ids = user_ids[:100]
    
    logger.info("=" * 70)
    logger.info(f"📤 Event 전송 시작: {len(user_ids)}명")
    logger.info("=" * 70)
    
    url = f"https://bot-api.kakao.com/v2/bots/{KAKAO_BOT_ID}/talk"
    
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 사용자 리스트 생성
    users = [{"type": user_type, "id": uid} for uid in user_ids]
    
    payload = {
        "event": {
            "name": KAKAO_EVENT_NAME
        },
        "user": users
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            
            if result.get("status") == "SUCCESS":
                task_id = result.get("taskId")
                logger.info(f"✅ Event API 호출 성공!")
                logger.info(f"   → Task ID: {task_id}")
                logger.info(f"   → 전송 대상: {len(user_ids)}명")
                logger.info(f"")
                logger.info(f"💡 실제 발송 여부 확인:")
                logger.info(f"   GET https://bot-api.kakao.com/v1/tasks/{task_id}")
                
                return {
                    'success': len(user_ids),
                    'failed': 0,
                    'total': len(user_ids),
                    'task_id': task_id
                }
            else:
                logger.error(f"❌ Event 전송 실패")
                logger.error(f"   → Status: {result.get('status')}")
                logger.error(f"   → Message: {result.get('message')}")
                return {
                    'success': 0,
                    'failed': len(user_ids),
                    'total': len(user_ids)
                }
        else:
            logger.error(f"❌ HTTP 에러: {response.status_code}")
            logger.error(f"   → {response.text}")
            return {
                'success': 0,
                'failed': len(user_ids),
                'total': len(user_ids)
            }
            
    except Exception as e:
        logger.error(f"❌ Event API 호출 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'success': 0,
            'failed': len(user_ids),
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
        user_type="botUserKey"  # 또는 "plusfriendUserKey"
    )
    
    logger.info("🎉 오전 8시 뉴스 푸시 완료!")
    
    return result


# ================================================================================
# 테스트 함수
# ================================================================================

def test_event_api(test_user_id: str):
    """
    Event API 테스트
    
    Args:
        test_user_id: 테스트할 사용자 ID
    """
    
    logger.info("=" * 70)
    logger.info("🧪 Event API 테스트")
    logger.info("=" * 70)
    
    logger.info(f"🔑 REST API Key: {'설정됨' if KAKAO_REST_API_KEY else '❌ 없음'}")
    logger.info(f"🤖 Bot ID: {KAKAO_BOT_ID if KAKAO_BOT_ID else '❌ 없음'}")
    logger.info(f"📝 Event Name: {KAKAO_EVENT_NAME}")
    logger.info(f"👤 Test User: {test_user_id[:10]}...")
    
    if not KAKAO_BOT_ID:
        logger.error("")
        logger.error("❌ KAKAO_BOT_ID 환경변수 없음!")
        logger.error("")
        logger.error("📋 Bot ID 찾는 방법:")
        logger.error("   1. https://business.kakao.com/ 접속")
        logger.error("   2. 챗봇 관리 → 설정")
        logger.error("   3. Bot ID 확인 (예: 5b3c85911073e946641ebb6d)")
        logger.error("   4. Render 환경변수에 KAKAO_BOT_ID 추가")
        return
    
    logger.info("")
    logger.info("⏳ 테스트 전송 중...")
    
    success = send_event_to_user(test_user_id, user_type="botUserKey")
    
    if success:
        logger.info("")
        logger.info("✅ 테스트 성공!")
        logger.info("📱 카카오톡에서 메시지를 확인하세요!")
        logger.info("")
        logger.info("💡 메시지 내용:")
        logger.info("   → '렉사 뉴스 자동 전송 테스트'")
    else:
        logger.error("")
        logger.error("❌ 테스트 실패!")
        logger.error("   → 위 에러 메시지를 확인하세요")


if __name__ == "__main__":
    # 테스트 실행
    import sys
    
    if len(sys.argv) > 1:
        test_user_id = sys.argv[1]
        test_event_api(test_user_id)
    else:
        print("사용법: python kakao_event_api_v2.py <user_id>")
