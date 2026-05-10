"""
Scheduler - Handles recurring, one-time, and limited tasks.
"""

import asyncio
import json
import os
import time
from datetime import datetime
from .config import WORKSPACE_DIR, MY_ID
from .logger import logger

TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")


async def run_condition(cmd):
    """Executes a guard command/script. Returns (Success, Output)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE_DIR
        )
        stdout, stderr = await proc.communicate()
        # Condition is met if exit code is 0
        return proc.returncode == 0, stdout.decode().strip()
    except Exception as e:
        logger.error(f"Condition command failed: {e}")
        return False, str(e)


async def scheduler_loop(bot_callback):
    """
    Background loop that checks tasks.json.
    Supports 'condition_cmd': only trigger agent if command returns exit code 0.
    """
    logger.info("Scheduler loop started.")
    
    while True:
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, 'r') as f:
                    tasks = json.load(f)
                
                now_dt = datetime.now()
                now_ts = time.time()
                current_time = now_dt.strftime("%H:%M")
                today = now_dt.strftime("%Y-%m-%d")
                day_name = now_dt.strftime("%A")
                
                updated = False
                remaining_tasks = []

                for task in tasks:
                    should_run_interval = False
                    
                    # Check if interval/time is due
                    interval = task.get("interval")
                    if interval:
                        last_run_ts = task.get("last_run_ts", 0)
                        if now_ts - last_run_ts >= int(interval):
                            should_run_interval = True
                    elif task.get("time") == current_time:
                        if task.get("last_run") != today:
                            days = task.get("days", [])
                            if not days or (day_name in days):
                                should_run_interval = True

                    if should_run_interval:
                        # Update timestamp immediately to avoid overlapping runs
                        task["last_run_ts"] = now_ts
                        task["last_run"] = today
                        updated = True

                        final_prompt = task["prompt"]
                        trigger_agent = True

                        # If there is a guard command, run it first (NO TOKENS USED)
                        if "condition_cmd" in task:
                            success, output = await run_condition(task["condition_cmd"])
                            if success:
                                # Condition met! Prepare prompt with script output
                                if output:
                                    final_prompt = final_prompt.replace("{{output}}", output)
                                    if "{{output}}" not in task["prompt"]:
                                        final_prompt += f"\n\nContext from condition script:\n{output}"
                            else:
                                # Condition not met, skip agent trigger
                                trigger_agent = False

                        if trigger_agent:
                            logger.info(f"Condition met, triggering agent for task: {task.get('name')}")
                            asyncio.create_task(bot_callback(final_prompt))

                        # Handle limited runs
                        if task.get("once") and trigger_agent: # Only delete if triggered
                            continue
                        if "count" in task and trigger_agent:
                            task["count"] = int(task["count"]) - 1
                            if task["count"] <= 0:
                                continue
                    
                    remaining_tasks.append(task)
                
                if updated:
                    with open(TASKS_FILE, 'w') as f:
                        json.dump(remaining_tasks, f, indent=4)
                        
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                
        await asyncio.sleep(30)


def init_tasks_file():
    """ Creates an empty tasks.json if it doesn't exist. """
    if not os.path.exists(TASKS_FILE):
        os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
        with open(TASKS_FILE, 'w') as f:
            json.dump([], f)
