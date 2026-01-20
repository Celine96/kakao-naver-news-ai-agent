"""
매일 오전 8시 부동산 뉴스 푸시 전송
- 모든 등록 사용자에게 카카오 Event API로 푸시
"""

import logging
import asyncio
from datetime import datetime

from user_management import get_all_user_ids, init_user_sheets
from kakao_event_api import send_daily_news_push

# ================================================================================
# 로깅 설정
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================================================================================
# 메인 함수
# ================================================================================

async def main():
    """
    매일 오전 8시 실행
    등록된 모든 사용자에게 뉴스 푸시 전송
    """
    
    logger.info("=" * 70)
    logger.info("🌅 REXA 오전 8시 뉴스 푸시 시작")
    logger.info(f"⏰ 현재 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 70)
    
    try:
        # 1. 사용자 시트 초기화
        logger.info("📋 사용자 시트 초기화 중...")
        success = init_user_sheets()
        
        if not success:
            logger.error("❌ 사용자 시트 초기화 실패 - 종료")
            return
        
        # 2. 푸시 받을 사용자 목록 조회
        logger.info("👥 푸시 수신 사용자 조회 중...")
        user_ids = get_all_user_ids(push_enabled_only=True)
        
        if not user_ids:
            logger.warning("⚠️ 푸시 받을 사용자 없음 - 종료")
            logger.info("💡 사용자가 봇을 사용하면 자동으로 등록됩니다")
            return
        
        logger.info(f"✅ 푸시 대상: {len(user_ids)}명")
        
        # 3. 카카오 Event API로 푸시 전송
        logger.info("📤 카카오 Event API 호출 중...")
        result = await send_daily_news_push(user_ids)
        
        # 4. 결과 출력
        logger.info("=" * 70)
        logger.info("🎉 푸시 전송 완료!")
        logger.info(f"   - 성공: {result['success']}명")
        logger.info(f"   - 실패: {result['failed']}명")
        logger.info(f"   - 총: {result['total']}명")
        logger.info("=" * 70)
        
        # 5. 성공률 계산
        if result['total'] > 0:
            success_rate = (result['success'] / result['total']) * 100
            logger.info(f"📊 성공률: {success_rate:.1f}%")
            
            if success_rate < 80:
                logger.warning("⚠️ 성공률이 낮습니다. 카카오 API 키를 확인하세요")
        
    except Exception as e:
        logger.error(f"❌ 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(main())
