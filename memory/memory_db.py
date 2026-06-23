import sqlite3
import os
from pathlib import Path
import sys

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
DB_PATH = BASE_DIR / "memory" / "memory.db"

def get_connection():
    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create conversation_history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user TEXT,
            assistant TEXT
        )
    """)
    
    # Create long_term_memory table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS long_term_memory (
            category TEXT,
            key TEXT,
            value TEXT,
            PRIMARY KEY (category, key)
        )
    """)
    
    # Create a table for compressed summaries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            summary TEXT,
            start_id INTEGER,
            end_id INTEGER
        )
    """)
    
    conn.commit()
    conn.close()

def append_conversation(user: str, assistant: str):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO conversation_history (timestamp, user, assistant) VALUES (?, ?, ?)",
        (timestamp, user, assistant)
    )
    conn.commit()
    conn.close()

def get_conversation_history(limit: int = None) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if limit:
        cursor.execute("SELECT * FROM conversation_history ORDER BY id DESC LIMIT ?", (limit,))
    else:
        cursor.execute("SELECT * FROM conversation_history ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]
        
    rows = cursor.fetchall()
    conn.close()
    
    # Convert to list of dicts and reverse to maintain chronological order for limited query
    history = []
    for row in reversed(rows):
        history.append(dict(row))
    return history

def update_long_term(category: str, key: str, value: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO long_term_memory (category, key, value) VALUES (?, ?, ?)",
        (category, key, value)
    )
    conn.commit()
    conn.close()

def get_long_term(category: str = None) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    if category:
        cursor.execute("SELECT key, value FROM long_term_memory WHERE category = ?", (category,))
    else:
        cursor.execute("SELECT category, key, value FROM long_term_memory")
    rows = cursor.fetchall()
    conn.close()
    
    result = {}
    if category:
        for row in rows:
            result[row["key"]] = {"value": row["value"]}
    else:
        for row in rows:
            cat = row["category"]
            if cat not in result:
                result[cat] = {}
            result[cat][row["key"]] = {"value": row["value"]}
            
    return result

def delete_conversation_range(start_id: int, end_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conversation_history WHERE id >= ? AND id <= ?", (start_id, end_id))
    conn.commit()
    conn.close()

def append_summary(summary: str, start_id: int, end_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    timestamp = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO history_summaries (timestamp, summary, start_id, end_id) VALUES (?, ?, ?, ?)",
        (timestamp, summary, start_id, end_id)
    )
    conn.commit()
    conn.close()

def get_summaries() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM history_summaries ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Initialize DB on import
init_db()
