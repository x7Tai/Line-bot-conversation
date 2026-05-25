import os
import re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from apscheduler.schedulers.background import BackgroundScheduler
import dateparser
import google.generativeai as genai

from db import init_db, set_user_state, get_user_state, clear_user_state, save_meeting
from utils import send_email_notification, generate_google_calendar_url
from calendar_api import create_calendar_event
from dotenv import load_dotenv

load_dotenv()

# 初始化 Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
generation_config = {
  "temperature": 0.1,
  "top_p": 0.95,
  "top_k": 64,
  "max_output_tokens": 1024,
  "response_mime_type": "application/json",
}
gemini_model = genai.GenerativeModel(
  model_name="gemini-1.5-flash",
  generation_config=generation_config,
)

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
        line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，已為您清除剛剛的對話記憶，我們可以重新開始囉！請問您要安排什麼行程呢？"))
        return
        
    state_record = get_user_state(user_id)
    
    try:
        temp_data = json.loads(state_record['temp_data']) if state_record and state_record['temp_data'] else {"topic": None, "time": None, "location": None}
    except:
        temp_data = {"topic": None, "time": None, "location": None}

    # 組合 Prompt 給 Gemini
    system_prompt = f"""
    你是一個行事曆秘書。你的任務是從使用者的輸入中擷取會議或行程的三個要素：
    1. topic (行程主題，例如開會、約會、吃飯、洗牙、踢足球)
    2. time (時間，必須包含日期概念，例如「明天下午3點」、「星期三早上9點」。如果使用者只說幾點(例如「下午5點」)，請當作未知，設定為 null)
    3. location (地點，例如麥當勞、會議室、海大籃球場)
    
    目前已經收集到的資訊 (若為null代表尚未提供)：
    {json.dumps(temp_data, ensure_ascii=False)}
    
    使用者的最新輸入是：「{text}」
    
    請結合已經收集到的資訊與最新輸入，更新並回傳最新的 JSON。
    如果無法確定某個要素，請保持 null。
    必須只回傳符合格式的 JSON 字串，不要有其他廢話。
    """

    try:
        response = gemini_model.generate_content(system_prompt)
        parsed_data = json.loads(response.text)
        
        # 把解析到的資料存回 temp_data
        if parsed_data.get('topic'): temp_data['topic'] = parsed_data['topic']
        if parsed_data.get('time'): temp_data['time'] = parsed_data['time']
        if parsed_data.get('location'): temp_data['location'] = parsed_data['location']
        
        # 檢查是否齊全
        missing = []
        if not temp_data.get('topic'): missing.append("行程主題")
        if not temp_data.get('time'): missing.append("時間 (請記得說哪一天喔！)")
        if not temp_data.get('location'): missing.append("地點")
        
        if missing:
            set_user_state(user_id, 'WAITING_FOR_DETAILS', temp_data=json.dumps(temp_data, ensure_ascii=False))
            reply_msg = f"我已經記下目前的資訊！\n"
            if temp_data.get('topic'): reply_msg += f"✅ 主題：{temp_data['topic']}\n"
            if temp_data.get('time'): reply_msg += f"✅ 時間：{temp_data['time']}\n"
            if temp_data.get('location'): reply_msg += f"✅ 地點：{temp_data['location']}\n"
            reply_msg += f"\n👉 請問您的「{'、'.join(missing)}」是？"
            
            line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))
            return
        else:
            # 全部都有了！
            process_meeting_booking(user_id, temp_data['topic'], temp_data['time'], temp_data['location'], reply_token)
            
    except Exception as e:
        print(f"Gemini API 錯誤: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，我的大腦剛才有點當機，請再試一次好嗎？"))
        return

if __name__ == "__main__":
    init_db()
    # 啟動伺服器
    app.run(port=5000)
