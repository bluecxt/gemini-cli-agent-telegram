"""
Gemini CLI Agent - Entry Point
"""

from core.memory import init_db
from core.tools import write_log
from core.telegram_handler import run_bot


if __name__ == '__main__':
    """ Initialize database """
    init_db()

    write_log("Gemini CLI Agent online.")

    """ Start Telegram bot """
    run_bot()
