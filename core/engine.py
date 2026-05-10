"""
Gemini Engine - Core reasoning loop with line-buffering and robust capture.
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
LIVE_BUFFERS = {}

SYSTEM_INSTRUCTIONS = """
Tu es un agent autonome ultra-concis.

RÈGLES ABSOLUES :
1. ACTIONS : Pour voir des fichiers ou toute action système, tu DOIS utiliser un outil (`list_directory`, `run_shell_command`). INTERDICTION formelle de simuler ou deviner le résultat.
2. RÉPONSE : Ne répète JAMAIS le contenu d'un résultat d'outil (comme un 'ls') dans ton texte. Le système s'en charge. Contente-toi de dire "Fait" ou d'analyser brièvement.
3. RÉFLEXION : Toujours analyser dans <thinking>...</thinking>.
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
    prompt = f"Résume en une phrase courte : {text}"
    args = ["gemini", "--prompt", prompt, "--output-format", "text"]
    try:
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        return out.decode().strip() or text[:200]
    except: return text[:200]

async def call_gemini_stream(prompt, chat_id, callback):
    """Reliable loop with stdbuf and stats tracking."""
    session_id = get_current_session(chat_id) or "latest"
    LIVE_BUFFERS[chat_id] = deque(maxlen=100)
    final_res_data = None
    accumulated_stdout = []

    full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\nUSER REQUEST: {prompt}"
    
    # Use stdbuf to ensure immediate output from gemini cli
    args = [
        "stdbuf", "-oL", "-eL",
        "gemini", "--prompt", "-", 
        "--output-format", "stream-json", 
        "--approval-mode", "yolo"
    ]
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
            
            logger.debug(f"RAW [{chat_id}]: {raw_line}")

            event = None
            try:
                start = raw_line.find('{"type":')
                if start != -1:
                    event = json.loads(raw_line[start:])
            except: pass

            if event:
                e_type = event.get("type")
                if e_type == "init" and event.get("session_id"):
                    set_current_session(chat_id, event.get("session_id"))
                
                if e_type == "result":
                    final_res_data = event.get("stats")
                
                if e_type == "tool_result" and not event.get("output") and accumulated_stdout:
                    event["output"] = "\n".join(accumulated_stdout)
                    accumulated_stdout = []

                await callback(e_type, event)
                
                if e_type == "tool_use":
                    accumulated_stdout = []
                continue

            # Non-JSON -> accumulated stdout
            accumulated_stdout.append(raw_line)
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
