"""
REXA 사용자 관리 시스템
- 사용자 ID 자동 등록
- User Type 저장 (botUserKey)
- Google Sheets 기반 저장
"""

import logging
import os
import json
from datetime import datetime
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

# Google Sheets용
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    logger.warning("gspread not installed. User management disabled.")

# ================================================================================
# 환경변수
# ================================================================================

GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
GOOGLE_SHEETS_USER_SHEET_ID = os.getenv("GOOGLE_SHEETS_USER_SHEET_ID")

# ================================================================================
# 글로벌 변수
# ================================================================================

user_sheet_client = None
user_worksheet = None

# ================================================================================
# Google Sheets 초기화 (사용자 관리용)
# ================================================================================

def init_user_sheets():
    """
    사용자 관리용 Google Sheets 초기화
    """
    global user_sheet_client, user_worksheet
    
    if not GSPREAD_AVAILABLE:
        logger.error("❌ gspread not installed")
        return False
    
    if not GOOGLE_SHEETS_CREDENTIALS:
        logger.error("❌ GOOGLE_SHEETS_CREDENTIALS 환경변수 없음")
        return False
    
    sheet_id = GOOGLE_SHEETS_USER_SHEET_ID or os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    
    if not sheet_id:
        logger.error("❌ Google Sheets ID 없음")
        return False
    
    try:
        logger.info("🔄 사용자 관리 시트 초기화 중...")
        
        creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        user_sheet_client = gspread.authorize(credentials)
        
        spreadsheet = user_sheet_client.open_by_key(sheet_id)
        
        # "users" 워크시트 찾기 또는 생성
        try:
            user_worksheet = spreadsheet.worksheet("users")
            logger.info("✅ 기존 'users' 시트 사용")
        except:
            user_worksheet = spreadsheet.add_worksheet(title="users", rows=1000, cols=10)
            logger.info("✅ 'users' 시트 생성")
        
        # 헤더 확인 및 생성
        try:
            headers = user_worksheet.row_values(1)
            if not headers or 'user_id' not in headers:
                user_worksheet.clear()
                user_worksheet.insert_row([
                    'user_id',
                    'user_type',        # botUserKey / plusfriendUserKey
                    'first_seen',
                    'last_seen',
                    'interaction_count',
                    'push_enabled',
                    'properties'
                ], 1)
                logger.info("✅ 사용자 시트 헤더 생성")
        except:
            user_worksheet.insert_row([
                'user_id',
                'user_type',
                'first_seen',
                'last_seen',
                'interaction_count',
                'push_enabled',
                'properties'
            ], 1)
        
        logger.info("✅ 사용자 관리 시트 초기화 완료")
        return True
        
    except Exception as e:
        logger.error(f"❌ 사용자 시트 초기화 실패: {e}")
        return False

# ================================================================================
# 사용자 등록 및 업데이트
# ================================================================================

def register_or_update_user(user_id: str, user_type: str = "botUserKey", properties: dict = None) -> bool:
    """
    사용자 등록 또는 업데이트
    
    Args:
        user_id: 카카오 사용자 ID
        user_type: "botUserKey" (기본값) 또는 "plusfriendUserKey"
        properties: 추가 속성 (선택)
    
    Returns:
        성공 여부
    """
    
    if not user_worksheet:
        success = init_user_sheets()
        if not success:
            logger.warning("⚠️ 사용자 시트 초기화 실패 - 등록 건너뜀")
            return False
    
    try:
        # 기존 사용자 찾기
        all_records = user_worksheet.get_all_records()
        
        existing_user = None
        row_index = None
        
        for idx, record in enumerate(all_records, 2):  # 헤더 제외 (2부터 시작)
            if record.get('user_id') == user_id:
                existing_user = record
                row_index = idx
                break
        
        timestamp = datetime.now().isoformat()
        
        if existing_user:
            # 기존 사용자 업데이트
            interaction_count = int(existing_user.get('interaction_count', 0)) + 1
            
            user_worksheet.update_cell(row_index, 4, timestamp)  # last_seen
            user_worksheet.update_cell(row_index, 5, interaction_count)  # interaction_count
            
            logger.info(f"✅ 사용자 업데이트: {user_id[:10]}... (총 {interaction_count}회)")
            
        else:
            # 신규 사용자 등록
            properties_json = json.dumps(properties) if properties else "{}"
            
            user_worksheet.append_row([
                user_id,
                user_type,      # botUserKey 저장
                timestamp,      # first_seen
                timestamp,      # last_seen
                1,              # interaction_count
                True,           # push_enabled (기본값: 활성화)
                properties_json
            ])
            
            logger.info(f"🆕 신규 사용자 등록: {user_id[:10]}... (type: {user_type})")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 사용자 등록/업데이트 실패: {e}")
        return False

# ================================================================================
# 사용자 목록 조회
# ================================================================================

def get_all_users(push_enabled_only: bool = True) -> List[Dict[str, str]]:
    """
    모든 사용자 정보 조회 (ID + Type)
    
    Args:
        push_enabled_only: True이면 푸시 활성화된 사용자만
    
    Returns:
        사용자 정보 리스트: [{"id": "user_id", "type": "botUserKey"}, ...]
    """
    
    if not user_worksheet:
        success = init_user_sheets()
        if not success:
            logger.warning("⚠️ 사용자 시트 초기화 실패")
            return []
    
    try:
        all_records = user_worksheet.get_all_records()
        
        users = []
        for record in all_records:
            user_id = record.get('user_id')
            user_type = record.get('user_type', 'botUserKey')  # 기본값
            push_enabled = record.get('push_enabled', True)
            
            if user_id:
                # push_enabled_only가 True이면 푸시 활성화된 사용자만
                if push_enabled_only:
                    if push_enabled in [True, 'TRUE', 'True', 'true', 1, '1']:
                        users.append({
                            "id": user_id,
                            "type": user_type
                        })
                else:
                    users.append({
                        "id": user_id,
                        "type": user_type
                    })
        
        logger.info(f"✅ 사용자 조회: 총 {len(users)}명")
        return users
        
    except Exception as e:
        logger.error(f"❌ 사용자 조회 실패: {e}")
        return []


def get_all_user_ids(push_enabled_only: bool = True) -> List[str]:
    """
    모든 사용자 ID 조회 (하위 호환성)
    
    Args:
        push_enabled_only: True이면 푸시 활성화된 사용자만
    
    Returns:
        사용자 ID 리스트
    """
    users = get_all_users(push_enabled_only)
    return [user["id"] for user in users]


def get_user_info(user_id: str) -> Optional[dict]:
    """
    특정 사용자 정보 조회
    
    Args:
        user_id: 카카오 사용자 ID
    
    Returns:
        사용자 정보 또는 None
    """
    
    if not user_worksheet:
        success = init_user_sheets()
        if not success:
            return None
    
    try:
        all_records = user_worksheet.get_all_records()
        
        for record in all_records:
            if record.get('user_id') == user_id:
                return record
        
        return None
        
    except Exception as e:
        logger.error(f"❌ 사용자 정보 조회 실패: {e}")
        return None

# ================================================================================
# 푸시 설정 관리
# ================================================================================

def set_push_enabled(user_id: str, enabled: bool) -> bool:
    """
    사용자 푸시 활성화/비활성화
    
    Args:
        user_id: 카카오 사용자 ID
        enabled: True=활성화, False=비활성화
    
    Returns:
        성공 여부
    """
    
    if not user_worksheet:
        success = init_user_sheets()
        if not success:
            return False
    
    try:
        all_records = user_worksheet.get_all_records()
        
        for idx, record in enumerate(all_records, 2):  # 헤더 제외
            if record.get('user_id') == user_id:
                user_worksheet.update_cell(idx, 6, enabled)  # push_enabled 컬럼
                logger.info(f"✅ 푸시 설정 변경: {user_id[:10]}... → {enabled}")
                return True
        
        logger.warning(f"⚠️ 사용자 없음: {user_id}")
        return False
        
    except Exception as e:
        logger.error(f"❌ 푸시 설정 실패: {e}")
        return False
