import os
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
import dateparser

from db import init_db, set_user_state, get_user_state, clear_user_state, save_meeting
from utils import send_email_notification, generate_google_calendar_url
from calendar_api import create_calendar_event
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# 初始化 LINE Bot API
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 啟動排程器
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.start()

def send_reminder(user_id, topic, meeting_time, location):
    """發送開會提醒"""
    try:
        reply_msg = (
            f"🔔 【提醒】您即將有一個行程！\n"
            f"主題：{topic}\n"
            f"時間：{meeting_time}\n"
            f"地點：{location}\n"
            "請記得準時出席喔！"
        )
        # 1. 推播 LINE 提醒訊息
        line_bot_api.push_message(user_id, TextSendMessage(text=reply_msg))
        
        # 2. 同時發送 Email 提醒
        user_email = os.getenv("GMAIL_USER")
        if user_email:
            send_email_notification(user_email, f"【即將開始】{topic}", meeting_time, location)
            print(f"已發送 Email 提醒給 {user_email}")
            
    except Exception as e:
        print(f"提醒發送失敗: {e}")

def process_meeting_booking(user_id, topic, meeting_time_str, location, reply_token):
    """處理完整的訂房/開會/約會邏輯"""
    try:
        # 嘗試解析時間 (使用 meeting_time_str 來解析)
        parsed_time = dateparser.parse(meeting_time_str, languages=['zh-Hant', 'zh'])
        
        if not parsed_time:
            # 如果解析不到，先給個預設時間，為了 Demo 方便
            parsed_time = datetime.now() + timedelta(days=1)
            
        # 儲存到資料庫
        save_meeting(user_id, topic, meeting_time_str, location)
        
        # 寄送 Email
        user_email = os.getenv("GMAIL_USER") # 測試用：寄給自己
        send_email_notification(user_email, topic, meeting_time_str, location)
        
        # 計算 2 小時前提醒的時間
        reminder_time = parsed_time - timedelta(hours=2)
        
        # 加入排程 (正式 2 小時前)
        if reminder_time > datetime.now():
            scheduler.add_job(send_reminder, 'date', run_date=reminder_time, args=[user_id, topic, meeting_time_str, location])
        
        # 自動建立 Google 行事曆 (並設定 2 小時前提醒)
        calendar_msg = ""
        gcal_url = create_calendar_event(topic, parsed_time, location)
        
        if gcal_url:
            calendar_msg = f"📅 行程已全自動加入您的 Google 行事曆！\n您可點此查看：{gcal_url}"
        else:
            # 如果自動建立失敗，退回手動連結作為保底
            gcal_url = generate_google_calendar_url(topic, meeting_time_str, location)
            calendar_msg = f"📅 點擊下方連結可手動加入 Google 行事曆：\n{gcal_url}"
        
        # 回覆使用者
        reply_msg = (
            "好的 我已經記錄下來了\n"
            "訂好會寄email通知您\n"
            "然後開會前2小時會提醒\n\n"
            f"{calendar_msg}"
        )
        
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
        # 清除狀態
        clear_user_state(user_id)
    except Exception as e:
        print(f"處理會議時發生錯誤: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，處理過程中發生了一點錯誤，請再試一次！"))



@app.route("/callback", methods=['POST'])
def callback():
    # 取得 X-Line-Signature header 值
    signature = request.headers['X-Line-Signature']
    # 取得 request body 為 text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    reply_token = event.reply_token
    
    if text in ["取消", "重來", "清除"]:
        clear_user_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，已為您清除剛剛的對話記憶，我們可以重新開始囉！"))
        return
        
    if text in ["取消", "重來", "清除"]:
        clear_user_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，已為您清除剛剛的對話記憶，我們可以重新開始囉！\n請問您要安排什麼行程呢？"))
        return
        
    state_record = get_user_state(user_id)
    current_state = state_record['state'] if state_record else None
    
    import json
    try:
        temp_data = json.loads(state_record['temp_data']) if state_record and state_record['temp_data'] else {}
    except:
        temp_data = {}

    if current_state is None:
        # 過濾打招呼
        greetings = ["你好", "安安", "哈囉", "hello", "hi", "新增", "行程", "預約", "安排", "建立行程"]
        if text.lower().strip() in greetings or len(text) < 2:
            reply_msg = "您好！我是您的專屬行事曆秘書 📅\n請問這次要幫您安排什麼行程呢？\n(請直接告訴我名稱，例如：「讀書會」、「剪頭髮」、「去海邊玩」)"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
            return
            
        # 第一步：把使用者的輸入當作主題
        topic = text.strip()
        set_user_state(user_id, 'WAITING_FOR_TIME', temp_data=json.dumps({"topic": topic}, ensure_ascii=False))
        reply_msg = f"好的，幫您安排「{topic}」！\n請問時間是哪一天幾點呢？\n(例如：星期三下午2點30分)"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
        return

    elif current_state == 'WAITING_FOR_TIME':
        # 第二步：把使用者的輸入當作時間
        topic = temp_data.get('topic', '行程')
        time_str = text.strip()
        
        # 檢查使用者有沒有給「日期」 (例如：明天, 星期三, 5號)
        date_keywords = ["一", "二", "三", "四", "五", "六", "日", "天", "號", "明", "後", "星期", "禮拜", "今"]
        if not any(k in time_str for k in date_keywords):
            reply_msg = f"您只有說「{time_str}」，請問是【哪一天】的 {time_str} 呢？\n(例如：明天、星期三、15號)"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
            return
            
        temp_data['time'] = time_str
        set_user_state(user_id, 'WAITING_FOR_LOCATION', temp_data=json.dumps(temp_data, ensure_ascii=False))
        reply_msg = f"收到！時間訂在「{time_str}」。\n最後，請問地點在哪裡呢？\n(只要告訴我地點名稱即可，任何地點都可以喔！)"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
        return

    elif current_state == 'WAITING_FOR_LOCATION':
        # 第三步：把使用者的輸入當作地點，並建立行程
        topic = temp_data.get('topic', '行程')
        meeting_time_str = temp_data.get('time', '')
        location = text.strip()
        
        # 建立行程 (process_meeting_booking 內部會處理完畢並 clear_user_state)
        process_meeting_booking(user_id, topic, meeting_time_str, location, reply_token)
        return

if __name__ == "__main__":
    init_db()
    # 啟動伺服器
    app.run(port=5000)
