"""
Memory - Handles session persistence and database initialization.
"""

import sqlite3
import os
from .config import WORKSPACE_DIR

DB_PATH = os.path.join(WORKSPACE_DIR, "data", "memory.db")


def init_db():
    """ Initializes the SQLite database for session mapping. """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            chat_id INTEGER PRIMARY KEY,
            current_session_id TEXT
        )
    """)
    conn.commit()
    conn.close()


def set_current_session(chat_id, session_id):
    """ Sets the active Gemini session ID for a Telegram chat. """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO sessions (chat_id, current_session_id)
        VALUES (?, ?)
    """, (chat_id, session_id))
    conn.commit()
    conn.close()


def get_current_session(chat_id):
    """ Retrieves the active Gemini session ID for a Telegram chat. """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT current_session_id FROM sessions WHERE chat_id = ?",
        (chat_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def save_memory(chat_id, role, content):
    """ Legacy compatibility for save_memory. gemini-cli handles history now. """
    pass


def search_memory(chat_id, query):
    """ Legacy compatibility for search_memory. """
    return ""


def get_recent_history(chat_id):
    """ Legacy compatibility for get_recent_history. """
    return ""
