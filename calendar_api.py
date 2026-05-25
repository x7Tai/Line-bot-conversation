import os
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Google 行事曆的權限範圍
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    """取得 Google Calendar API 授權並建立服務"""
    creds = None
    # token.json 儲存使用者的 access token 與 refresh token
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # 如果沒有有效的憑證，就讓使用者登入
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("找不到 credentials.json 檔案")
                return None
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            # 在本機開啟瀏覽器進行授權 (port 0 代表自動尋找可用 port)
            creds = flow.run_local_server(port=0)
        # 把成功的憑證存起來，下次就不用再登入
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"建立 Calendar Service 失敗: {e}")
        return None

def create_calendar_event(topic, start_time, location):
    """在 Google 行事曆中建立自動行程"""
    service = get_calendar_service()
    if not service:
        return False
        
    # 設定結束時間與開始時間相同，避免佔用一小時
    end_time = start_time
    
    event = {
        'summary': topic,
        'location': location,
        'description': '這是由 LINE Bot 自動為您建立的會議行程。',
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'Asia/Taipei',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Asia/Taipei',
        },
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': 24 * 60},
                {'method': 'popup', 'minutes': 120}, # 開會前 2 小時 (120分鐘) 彈出提醒
            ],
        },
    }

    try:
        event_result = service.events().insert(calendarId='primary', body=event).execute()
        print(f"成功建立行事曆: {event_result.get('htmlLink')}")
        return event_result.get('htmlLink')
    except Exception as e:
        print(f"建立行事曆事件失敗: {e}")
        return False
