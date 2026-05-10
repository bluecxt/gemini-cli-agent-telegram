"""
Gemini Engine - Core reasoning loop with robust event decoding.
"""

import json
import asyncio
import os
import re
from collections import deque
from .memory import get_current_session, set_current_session
from .logger import logger

ACTIVE_SUBPROCESSES = {}
STOP_SIGNAL = {}
LIVE_BUFFERS = {}  # chat_id: deque of strings

SYSTEM_INSTRUCTIONS = """
Tu es un agent autonome ultra-concis.

RÈGLES ABSOLUES :
1. ACTIONS : Pour voir des fichiers ou toute action système, tu DOIS utiliser un outil (`list_directory`, `run_shell_command`). INTERDICTION de deviner.
2. RÉFLEXION : Analyse dans <thinking>...</thinking>.
3. RÉPONSE : En dehors de <thinking>, écris uniquement en FRANÇAIS.
4. HISTORIQUE : Documente tes changements dans `/app/workspace/HISTORY.md` via shell.
5. IMAGES : [SEND_IMAGE: /chemin/vers/image.png]
"""

def register_process(chat_id, proc):
    if chat_id not in ACTIVE_SUBPROCESSES:
        ACTIVE_SUBPROCESSES[chat_id] = []
    ACTIVE_SUBPROCESSES[chat_id].append(proc)

def unregister_process(chat_id, proc):
    if chat_id in ACTIVE_SUBPROCESSES:
        try: ACTIVE_SUBPROCESSES[chat_id].remove(proc)
        except ValueError: pass

def clear_live_buffer(chat_id):
    if chat_id in LIVE_BUFFERS:
        LIVE_BUFFERS[chat_id].clear()

async def _read_stderr(stream, chat_id):
    if chat_id not in LIVE_BUFFERS:
        LIVE_BUFFERS[chat_id] = deque(maxlen=100)
    while True:
        try:
            line = await stream.readline()
            if not line: break
            text = line.decode().strip()
            if text:
                LIVE_BUFFERS[chat_id].append(f"stderr: {text}")
        except: break

async def get_short_summary(text):
    if not text or len(text) < 100: return text
    prompt = f"Résume en une phrase : {text}"
    args = ["gemini", "--prompt", prompt, "--output-format", "text"]
    try:
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        return out.decode().strip() or text[:200]
    except: return text[:200]

async def call_gemini_stream(prompt, chat_id, callback):
    """Loop with robust full-line JSON detection and stats recovery."""
    session_id = get_current_session(chat_id) or "latest"
    LIVE_BUFFERS[chat_id] = deque(maxlen=100)
    final_res_data = None

    full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\nUSER REQUEST: {prompt}"
    args = ["gemini", "--prompt", "-", "--output-format", "stream-json", "--approval-mode", "yolo"]
    if session_id and session_id != "fresh_session":
        args.extend(["--resume", session_id])

    env = os.environ.copy()
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    env["PAGER"] = "cat"

    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    register_process(chat_id, proc)
    stderr_task = asyncio.create_task(_read_stderr(proc.stderr, chat_id))

    try:
        proc.stdin.write(full_prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        while True:
            line = await proc.stdout.readline()
            if not line: break
            raw_line = line.decode().strip()
            if not raw_line: continue
            
            logger.debug(f"RAW_STDOUT [{chat_id}]: {raw_line}")

            # Robust detection: If line starts with JSON signature, attempt full parse
            if raw_line.startswith('{"type":'):
                try:
                    event = json.loads(raw_line)
                    if event.get("type") == "init" and event.get("session_id"):
                        set_current_session(chat_id, event.get("session_id"))
                    
                    if event.get("type") == "result":
                        final_res_data = event.get("stats")
                    
                    await callback(event.get("type"), event)
                    continue 
                except json.JSONDecodeError:
                    # If JSON is broken/mixed, try partial extraction
                    match = re.search(r'(\{"type":.*?\})', raw_line)
                    if match:
                        try:
                            event = json.loads(match.group(1))
                            await callback(event.get("type"), event)
                            continue
                        except: pass

            # Treat as raw output (TTY/Bash/Logs)
            LIVE_BUFFERS[chat_id].append(raw_line)
            await callback("raw_stdout", {"content": raw_line})

        await proc.wait()
        await stderr_task
        return proc.returncode, final_res_data
    except Exception as e:
        logger.exception("ENGINE ERROR")
        return -1, None
    finally:
        unregister_process(chat_id, proc)
        if proc.returncode is None:
            try: proc.kill()
            except: pass
