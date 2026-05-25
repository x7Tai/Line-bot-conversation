import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD")

def send_email_notification(to_email, topic, meeting_time, location):
    """寄送 Email 通知"""
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("未設定 Gmail 帳號密碼，略過寄信。")
        return False
        
    subject = f"【會議通知】{topic}"
    body = f"""
    您好，
    
    這是您的會議通知：
    
    主題：{topic}
    時間：{meeting_time}
    地點：{location}
    
    請準時參加！
    """
    
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(GMAIL_USER, to_email, text)
        server.quit()
        return True
    except Exception as e:
        print(f"寄信失敗: {e}")
        return False

def generate_google_calendar_url(topic, meeting_time, location):
    """產生新增至 Google Calendar 的連結"""
    # 將 meeting_time 轉為格式 (這裡假設 meeting_time 是字串，若需精確格式需解析 datetime)
    # 為簡化，我們直接將參數帶入 text, dates, details, location
    base_url = "https://calendar.google.com/calendar/render?action=TEMPLATE"
    params = {
        "text": topic,
        "details": f"時間: {meeting_time}\n請記得準時出席。",
        "location": location
    }
    # URL Encoding
    query_string = urllib.parse.urlencode(params)
    return f"{base_url}&{query_string}"
