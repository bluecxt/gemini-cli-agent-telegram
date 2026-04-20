"""
Scheduler - Handles recurring, one-time, and limited tasks.
"""

import asyncio
import json
import os
from datetime import datetime
from .config import WORKSPACE_DIR, MY_ID
from .logger import logger

TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")


async def scheduler_loop(bot_callback):
    """
    Background loop that checks tasks.json every minute.
    Supports 'once': true and 'count': N.
    """
    logger.info("Scheduler loop started.")
    
    while True:
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, 'r') as f:
                    tasks = json.load(f)
                
                now = datetime.now()
                current_time = now.strftime("%H:%M")
                today = now.strftime("%Y-%m-%d")
                
                updated = False
                remaining_tasks = []

                for task in tasks:
                    # Check if it's time to run
                    should_run = (
                        task.get("time") == current_time and 
                        task.get("last_run") != today
                    )

                    if should_run:
                        logger.info(f"Triggering task: {task.get('name', 'unnamed')}")
                        
                        # Trigger the agent
                        asyncio.create_task(bot_callback(task["prompt"]))
                        
                        # Update execution status
                        task["last_run"] = today
                        updated = True

                        # Handle limited runs
                        if task.get("once"):
                            # Delete task immediately by not adding to remaining
                            continue
                        
                        if "count" in task:
                            task["count"] = int(task["count"]) - 1
                            if task["count"] <= 0:
                                # Delete task if count reached 0
                                continue
                    
                    remaining_tasks.append(task)
                
                if updated:
                    with open(TASKS_FILE, 'w') as f:
                        json.dump(remaining_tasks, f, indent=4)
                        
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                
        await asyncio.sleep(60)


def init_tasks_file():
    """ Creates an empty tasks.json if it doesn't exist. """
    if not os.path.exists(TASKS_FILE):
        os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
        with open(TASKS_FILE, 'w') as f:
            json.dump([], f)
