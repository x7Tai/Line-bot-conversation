import sqlite3
from datetime import datetime

DB_FILE = "bot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 建立會議記錄表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            topic TEXT,
            meeting_time TEXT,
            location TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 建立使用者狀態表（用來記錄對話上下文）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            temp_data TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

def set_user_state(user_id, state, temp_data=""):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_states (user_id, state, temp_data, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state=excluded.state,
            temp_data=excluded.temp_data,
            updated_at=excluded.updated_at
    """, (user_id, state, temp_data, datetime.now()))
    conn.commit()
    conn.close()

def get_user_state(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT state, temp_data FROM user_states WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"state": row[0], "temp_data": row[1]}
    return None

def clear_user_state(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def save_meeting(user_id, topic, meeting_time, location):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO meetings (user_id, topic, meeting_time, location, status)
        VALUES (?, ?, ?, ?, 'scheduled')
    """, (user_id, topic, meeting_time, location))
    meeting_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return meeting_id

if __name__ == "__main__":
    init_db()
    print("資料庫初始化完成！")
