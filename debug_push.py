"""
디버깅용 푸시 알림 스크립트 - 상세 로그 포함
"""

import os
import requests
import gspread
from google.oauth2.service_account import Credentials
import json
import time
from datetime import datetime

# ================================================================================
# 로깅 설정
# ================================================================================

def log(message):
    """타임스탬프와 함께 로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

# ================================================================================
# 환경변수 확인
# ================================================================================

log("=" * 70)
log("🔍 환경변수 확인 시작")
log("=" * 70)

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_EVENT_NAME = os.getenv("KAKAO_EVENT_NAME", "event02")
GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
GOOGLE_SHEETS_USER_SHEET_ID = os.getenv("GOOGLE_SHEETS_USER_SHEET_ID")

log(f"✅ KAKAO_REST_API_KEY: {'설정됨' if KAKAO_REST_API_KEY else '❌ 없음'}")
if KAKAO_REST_API_KEY:
    log(f"   → 키 길이: {len(KAKAO_REST_API_KEY)}")
    log(f"   → 앞 10자: {KAKAO_REST_API_KEY[:10]}...")
    
log(f"✅ KAKAO_EVENT_NAME: {KAKAO_EVENT_NAME}")
log(f"✅ GOOGLE_SHEETS_CREDENTIALS: {'설정됨' if GOOGLE_SHEETS_CREDENTIALS else '❌ 없음'}")
log(f"✅ GOOGLE_SHEETS_USER_SHEET_ID: {'설정됨' if GOOGLE_SHEETS_USER_SHEET_ID else '❌ 없음'}")

if not all([KAKAO_REST_API_KEY, GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_USER_SHEET_ID]):
    log("❌ 환경변수 누락! 종료합니다.")
    exit(1)

# ================================================================================
# Google Sheets 연결
# ================================================================================

log("=" * 70)
log("📊 Google Sheets 연결 시작")
log("=" * 70)

try:
    credentials_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
    gc = gspread.authorize(credentials)
    
    log("✅ Google Sheets 인증 성공")
    
    # 시트 열기
    sheet = gc.open_by_key(GOOGLE_SHEETS_USER_SHEET_ID)
    worksheet = sheet.worksheet("users")
    
    log(f"✅ 워크시트 '{worksheet.title}' 열림")
    
    # 모든 데이터 가져오기
    all_records = worksheet.get_all_records()
    log(f"✅ 총 레코드 수: {len(all_records)}")
    
    # 푸시 활성화된 사용자만 필터링
    active_users = [
        record for record in all_records 
        if record.get("push_enabled", "").upper() == "TRUE"
    ]
    
    log(f"✅ 푸시 활성 사용자: {len(active_users)}명")
    
    if len(active_users) == 0:
        log("❌ 푸시 받을 사용자가 없습니다!")
        log("💡 해결: 카카오톡 챗봇을 사용하여 사용자 등록")
        exit(0)
    
    # 사용자 목록 출력
    for idx, user in enumerate(active_users[:5], 1):  # 최대 5명만 출력
        log(f"   [{idx}] user_id: {user.get('user_id', 'N/A')[:20]}...")
    
    if len(active_users) > 5:
        log(f"   ... 외 {len(active_users) - 5}명")
    
except Exception as e:
    log(f"❌ Google Sheets 오류: {e}")
    import traceback
    log(traceback.format_exc())
    exit(1)

# ================================================================================
# 카카오 Event API 테스트
# ================================================================================

log("=" * 70)
log("📤 카카오 Event API 테스트")
log("=" * 70)

# API 엔드포인트 (현재 사용 중)
url = "https://kapi.kakao.com/v1/api/talk/bot/event/send"
log(f"🔗 API URL: {url}")

headers = {
    "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}",
    "Content-Type": "application/json"
}

log(f"📝 Event Name: {KAKAO_EVENT_NAME}")

# 테스트: 첫 번째 사용자에게만 전송
test_user = active_users[0]
test_user_id = test_user.get("user_id")

log(f"🧪 테스트 사용자: {test_user_id[:20]}...")

payload = {
    "event": KAKAO_EVENT_NAME,
    "user": test_user_id,
    "params": {}
}

log(f"📦 Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

try:
    log("⏳ API 호출 중...")
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    
    log(f"📊 응답 상태 코드: {response.status_code}")
    log(f"📊 응답 헤더: {dict(response.headers)}")
    
    try:
        response_json = response.json()
        log(f"📊 응답 본문: {json.dumps(response_json, indent=2, ensure_ascii=False)}")
    except:
        log(f"📊 응답 본문 (텍스트): {response.text}")
    
    if response.status_code == 200:
        log("✅ API 호출 성공!")
        log("📱 카카오톡에서 푸시 알림을 확인하세요!")
    else:
        log(f"❌ API 호출 실패!")
        log(f"💡 상태 코드: {response.status_code}")
        
        if response.status_code == 401:
            log("💡 401 Unauthorized: REST API Key 확인 필요")
            log("   → 카카오 개발자 콘솔에서 REST API 키 재확인")
        elif response.status_code == 403:
            log("💡 403 Forbidden: 권한 문제")
            log("   → 챗봇 설정 또는 Event 설정 확인")
        elif response.status_code == 404:
            log("💡 404 Not Found: API 엔드포인트 또는 Event 이름 확인")
            log(f"   → Event Name: {KAKAO_EVENT_NAME}")
            log("   → 카카오 비즈니스에서 Event 설정 확인")
        
except requests.exceptions.Timeout:
    log("❌ 타임아웃: API 응답 없음")
except Exception as e:
    log(f"❌ API 호출 오류: {e}")
    import traceback
    log(traceback.format_exc())

log("=" * 70)
log("🏁 디버깅 완료")
log("=" * 70)
