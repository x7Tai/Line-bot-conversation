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
        line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，已為您清除剛剛的對話記憶，我們可以重新開始囉！"))
        return
        
    state_record = get_user_state(user_id)
    current_state = state_record['state'] if state_record else None
    temp_data = state_record['temp_data'] if state_record else ""
    
    # 支援多種情境
    event_keywords = ["開會", "約會", "吃飯", "聚餐", "上課", "打球", "討論", "看電影", "行程", "安排", "預約", "見面"]
    
    # 如果不在等待狀態，且沒提到任何行程，直接回覆預設訊息
    if current_state != 'WAITING_FOR_DETAILS' and not any(k in text for k in event_keywords):
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text="如果您要安排任何行程（開會、約會、聚餐等），可以告訴我時間和地點喔！例如：「這週一下午4點 延平技術大樓817開會」")
        )
        return

    # 結合歷史輸入與本次輸入
    if current_state == 'WAITING_FOR_DETAILS':
        combined_text = f"{temp_data} {text}"
    else:
        combined_text = text
        
    # 擷取主題
    topic = "行程"
    for k in ["開會", "約會", "吃飯", "聚餐", "上課", "打球", "討論", "看電影", "見面"]:
        if k in combined_text:
            topic = k
            break

    # 定義關鍵字
    loc_keywords = ["樓", "地點", "教室", "817", "會議室", "餐廳", "餐", "館", "公園", "路", "街", "店", "家", "大學", "學校", "海大", "公司", "辦公室", "中心", "大樓", "咖啡", "圖書館"]
    date_keywords = ["一", "二", "三", "四", "五", "六", "日", "天", "號", "明", "後"]
    time_keywords = ["點", "分", "早上", "下午", "晚上", "點半"]

    # 檢查是否具備所有條件
    has_date = any(keyword in combined_text for keyword in date_keywords)
    has_time = any(keyword in combined_text for keyword in time_keywords)
    has_location = any(keyword in combined_text for keyword in loc_keywords)

    if has_date and has_time and has_location:
        # 資訊齊全！我們從 combined_text 中「萃取」出時間與地點
        extracted_location = ""
        time_chunks = []
        
        chunks = combined_text.split()
        if len(chunks) == 1:
            extracted_location = combined_text
            meeting_time_str = combined_text
        else:
            for chunk in chunks:
                if any(k in chunk for k in loc_keywords):
                    extracted_location = chunk
                elif any(k in chunk for k in date_keywords + time_keywords + ["星期", "禮拜"]):
                    time_chunks.append(chunk)
            
            if not extracted_location:
                extracted_location = "未指定地點"
            
            meeting_time_str = " ".join(time_chunks) if time_chunks else "未指定時間"

        process_meeting_booking(user_id, topic, meeting_time_str, extracted_location, reply_token)

    else:
        # 資訊依然不齊全，更新歷史紀錄並繼續詢問
        set_user_state(user_id, 'WAITING_FOR_DETAILS', temp_data=combined_text)
        
        if not has_date and not has_time and not has_location:
            reply_text = f"請問這個{topic}具體是哪一天、幾點幾分，以及地點在哪裡呢？"
        else:
            got = []
            if has_date: got.append("日期")
            if has_time: got.append("時間")
            if has_location: got.append("地點")
            
            missing = []
            if not has_date: missing.append("哪一天（例如星期幾）")
            if not has_time: missing.append("幾點幾分")
            if not has_location: missing.append("在哪個地點")
            
            reply_text = f"收到{'與'.join(got)}了！但請問這個{topic}具體是{'、'.join(missing)}呢？"
            
        line_bot_api.reply_message(
            reply_token, 
            TextSendMessage(text=reply_text)
        )

if __name__ == "__main__":
    init_db()
    # 啟動伺服器
    app.run(port=5000)
