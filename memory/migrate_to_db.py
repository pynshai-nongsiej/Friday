import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.append("/Users/pynshainongsiej/Mark-XXX")

from memory.memory_db import DB_PATH, init_db, update_long_term
from memory.memory_manager import CONVERSATION_HISTORY_PATH, MEMORY_PATH

def migrate():
    print("Starting migration to SQLite database...")
    init_db()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Migrate conversation history
    if CONVERSATION_HISTORY_PATH.exists():
        print(f"Loading conversation history from {CONVERSATION_HISTORY_PATH}")
        try:
            with open(CONVERSATION_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
                if isinstance(history, list):
                    print(f"Found {len(history)} entries.")
                    for entry in history:
                        user = entry.get("user", "")
                        assistant = entry.get("assistant", "")
                        timestamp = entry.get("timestamp", __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
                        
                        cursor.execute(
                            "INSERT INTO conversation_history (timestamp, user, assistant) VALUES (?, ?, ?)",
                            (timestamp, user, assistant)
                        )
                    conn.commit()
                    print("Conversation history migrated.")
        except Exception as e:
            print(f"Error migrating conversation history: {e}")
            
    # 2. Migrate long term memory
    if MEMORY_PATH.exists():
        print(f"Loading long term memory from {MEMORY_PATH}")
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                memory = json.load(f)
                if isinstance(memory, dict):
                    for category, entries in memory.items():
                        if isinstance(entries, dict):
                            for key, entry in entries.items():
                                val = entry.get("value", "") if isinstance(entry, dict) else str(entry)
                                update_long_term(category, key, val)
                    print("Long term memory migrated.")
        except Exception as e:
            print(f"Error migrating long term memory: {e}")
            
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
