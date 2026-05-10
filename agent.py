"""
Gemini CLI Agent - Entry Point
"""

import asyncio
import threading
from core.memory import init_db
from core.tools import write_log
from core.telegram_handler import run_bot, trigger_scheduled_task
from core.scheduler import scheduler_loop, init_tasks_file
from core.webhooks import start_webhook_server


if __name__ == '__main__':
    """ Initialize components """
    init_db()
    init_tasks_file()

    write_log("Gemini CLI Agent online.")

    # We use a trick: start scheduler in the background of the event loop
    # but run_bot uses its own loop.
    async def main_async_tasks(bot_callback):
        # Start Webhook server
        asyncio.create_task(start_webhook_server(port=8080))
        # Start Scheduler
        await scheduler_loop(bot_callback)

    run_bot(main_async_tasks)
