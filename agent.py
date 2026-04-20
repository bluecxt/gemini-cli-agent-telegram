"""
Gemini CLI Agent - Entry Point
"""

import asyncio
import threading
from core.memory import init_db
from core.tools import write_log
from core.telegram_handler import run_bot, trigger_scheduled_task
from core.scheduler import scheduler_loop, init_tasks_file


if __name__ == '__main__':
    """ Initialize components """
    init_db()
    init_tasks_file()

    write_log("Gemini CLI Agent online.")

    """ Start Telegram bot in the main thread (blocking) """
    """ The scheduler will be started by the bot's post_init or here if possible """
    
    # We use a trick: start scheduler in the background of the event loop
    # but run_bot uses its own loop. Let's fix telegram_handler to handle this.
    run_bot(scheduler_loop)
