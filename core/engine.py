"""
Gemini Engine - Core reasoning loop compatible with latest JSON format.
"""

import json
import asyncio
from .memory import get_current_session, set_current_session
from .logger import logger

ACTIVE_SUBPROCESSES = {}
STOP_SIGNAL = {}

SYSTEM_INSTRUCTIONS = """
You are the Gemini CLI Agent, a high-performance autonomous engineering assistant.
You operate within a Docker container.

HIERARCHY & PERSISTENCE RULES:
1. USE `/app/workspace` for ALL long-term data, source code, and project files.
   This folder is persistent and survives container resets (docker-compose down).
2. USE `/app/tmp` for temporary working notes or transient data.
3. You have root access inside this container. You can install packages (pip, apt)
   if needed, but they will be lost on container reset.
4. ALWAYS prioritize working within `/app/workspace` for important project elements.

MANDATORY FORMATTING:
- ALL internal reasoning must be inside <thinking> tags.
- Use <b>bold</b>, <i>italic</i> and <code>code</code> for Telegram formatting.
"""


def register_process(chat_id, proc):
    if chat_id not in ACTIVE_SUBPROCESSES:
        ACTIVE_SUBPROCESSES[chat_id] = []
    ACTIVE_SUBPROCESSES[chat_id].append(proc)


def unregister_process(chat_id, proc):
    if chat_id in ACTIVE_SUBPROCESSES:
        try:
            ACTIVE_SUBPROCESSES[chat_id].remove(proc)
        except ValueError:
            pass


async def call_gemini_stream(prompt, chat_id, callback):
    """
    Calls Gemini CLI in stream-json mode and processes events.
    """
    session_id = get_current_session(chat_id) or "latest"

    """
    Inject system instructions into the prompt for every turn
    if it's a fresh session or resumption.
    """
    full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\nUSER REQUEST: {prompt}"

    args = [
        "gemini", "--prompt", "-",
        "--output-format", "stream-json",
        "--approval-mode", "yolo"
    ]

    if session_id and session_id != "fresh_session":
        args.extend(["--resume", session_id])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    register_process(chat_id, proc)

    last_error = ""

    try:
        proc.stdin.write(full_prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        while True:
            line = await proc.stdout.readline()
            if not line:
                break

            raw_line = line.decode().strip()
            if not raw_line or not raw_line.startswith("{"):
                continue

            try:
                event = json.loads(raw_line)
                e_type = event.get("type")

                if e_type == "init":
                    sid = event.get("session_id")
                    if sid:
                        set_current_session(chat_id, sid)

                await callback(e_type, event)

            except json.JSONDecodeError:
                continue

        stderr_data = await proc.stderr.read()
        if stderr_data:
            last_error = stderr_data.decode().strip()

        await proc.wait()
        return proc.returncode, last_error

    except Exception as e:
        logger.exception("ENGINE ERROR")
        return -1, str(e)
    finally:
        unregister_process(chat_id, proc)
        if proc.returncode is None:
            try:
                proc.kill()
            except:
                pass
