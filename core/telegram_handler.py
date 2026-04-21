"""
Telegram Handler - Manages user interaction and Gemini streaming.
"""

import asyncio
import os
import re
import subprocess
import sys
from telegram import (
    Update,
    Message,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

from .config import TOKEN, MY_ID, TMP_DIR, WORKSPACE_DIR
from .memory import set_current_session
from .engine import call_gemini_stream, ACTIVE_SUBPROCESSES, STOP_SIGNAL
from .logger import logger

CHAT_LOCKS = {}
GLOBAL_APPLICATION = None
ACTIVE_STATUS_MSGS = {}  # chat_id: Message object

TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")

# Mapping for cleaner action reporting
TOOL_MAPPING = {
    "list_directory": "ReadFolder",
    "read_file": "ReadFile",
    "write_file": "WriteFile",
    "replace": "EditFile",
    "run_shell_command": "Bash",
    "grep_search": "Search",
    "google_web_search": "WebSearch",
    "web_fetch": "WebRead"
}


def is_not_user(update: Update) -> bool:
    """Checks if the message is from the authorized user."""
    user_id = update.effective_user.id
    if user_id != MY_ID:
        logger.warning(f"Unauthorized access attempt from ID: {user_id}")
        return True
    return False


async def _handle_attachments(message: Message) -> str:
    """Downloads attachments and returns the updated user input string."""
    user_input = message.text or ""

    if not (message.photo or message.document):
        return user_input

    try:
        file_obj = await (
            message.photo[-1] if message.photo
            else message.document
        ).get_file()

        file_path = os.path.join(
            TMP_DIR,
            file_obj.file_path.split('/')[-1]
        )
        await file_obj.download_to_drive(file_path)

        caption = message.caption or "Analysis"
        user_input = f"{caption}\n[FILE: {file_path}]"
        logger.info(f"Telegram Attachment downloaded: {file_path}")
    except Exception as e:
        logger.error(f"Error receiving file: {e}")

    return user_input


def _format_html_response(text: str) -> str:
    """Cleans AI tags and converts Markdown to HTML."""
    if not text:
        return ""

    # Remove thinking tags
    clean_text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    clean_text = re.sub(r"<thinking>.*$", "", clean_text, flags=re.DOTALL)
    clean_text = re.sub(r"\[Thought:.*?\]", "", clean_text, flags=re.DOTALL).strip()

    if not clean_text:
        return ""

    # Escape HTML
    clean_text = clean_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Markdown -> HTML
    clean_text = re.sub(r"```(?:[\w]+)?\n?(.*?)```", r"<code>\1</code>", clean_text, flags=re.DOTALL)
    clean_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", clean_text)
    clean_text = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"<i>\1</i>", clean_text)
    clean_text = re.sub(r"`(.*?)`", r"<code>\1</code>", clean_text)

    # Restore tags
    replacements = {"&lt;b&gt;": "<b>", "&lt;/b&gt;": "</b>", "&lt;i&gt;": "<i>", "&lt;/i&gt;": "</i>", "&lt;code&gt;": "<code>", "&lt;/code&gt;": "</code>", "&lt;pre&gt;": "<pre>", "&lt;/pre&gt;": "</pre>"}
    for old, new in replacements.items():
        clean_text = clean_text.replace(old, new)

    return clean_text


def _balance_tags(text: str):
    """Closes unclosed tags for splitting."""
    tags = ['b', 'i', 'code', 'pre']
    stack = []
    found = re.findall(r'<(/?)(b|i|code|pre)(?:\s.*?)?>', text)
    for is_closing, tag in found:
        if is_closing:
            if stack and stack[-1] == tag: stack.pop()
        else: stack.append(tag)
    suffix = "".join([f"</{t}>" for t in reversed(stack)])
    prefix = "".join([f"<{t}>" for t in stack])
    return text + suffix, prefix


async def _send_long_message(message_obj, text: str, **kwargs):
    """Sends long message in chunks."""
    if not text: return
    limit = 3900
    current_prefix = ""
    is_html = kwargs.get("parse_mode") == "HTML"
    while len(text) > 0:
        if len(text) <= limit:
            chunk = current_prefix + text
            text = ""
        else:
            split_at = text.rfind("\n", 0, limit)
            if split_at == -1: split_at = text.rfind(" ", 0, limit)
            if split_at == -1: split_at = limit
            raw_chunk = text[:split_at]
            if is_html:
                balanced_chunk, next_prefix = _balance_tags(raw_chunk)
                chunk = current_prefix + balanced_chunk
                current_prefix = next_prefix
            else: chunk = raw_chunk
            text = text[split_at:].lstrip()
        try:
            if hasattr(message_obj, "reply_text"): await message_obj.reply_text(chunk, **kwargs)
            else: await message_obj.send_message(chat_id=MY_ID, text=chunk, **kwargs)
        except Exception as e:
            logger.error(f"Error: {e}")
            try:
                if hasattr(message_obj, "reply_text"): await message_obj.reply_text(chunk)
                else: await message_obj.send_message(chat_id=MY_ID, text=chunk)
            except: pass


def _summarize_actions(actions):
    """Summarizes tool actions."""
    if not actions: return ""
    summary_lines, current_group = [], []
    for name, param, has_text_before in actions:
        if has_text_before or not current_group:
            if current_group: summary_lines.append(", ".join(current_group))
            current_group = [f"{name} {param}"]
        else: current_group.append(f"{name} {param}")
    if current_group: summary_lines.append(", ".join(current_group))
    return "\n".join([f"🛠️ <i>{line}</i>" for line in summary_lines])


async def _refresh_thinking_msg(chat_id, last_text="🤔 <b>Thinking...</b>"):
    """Moves status message to bottom."""
    if chat_id in ACTIVE_STATUS_MSGS:
        try: await ACTIVE_STATUS_MSGS[chat_id].delete()
        except: pass
    if GLOBAL_APPLICATION:
        new_msg = await GLOBAL_APPLICATION.bot.send_message(chat_id=chat_id, text=last_text, parse_mode="HTML")
        ACTIVE_STATUS_MSGS[chat_id] = new_msg
        return new_msg
    return None


async def trigger_scheduled_task(prompt: str):
    """Automated task runner."""
    if not GLOBAL_APPLICATION: return
    logger.info(f"Scheduled task: {prompt[:50]}")
    status_msg = await GLOBAL_APPLICATION.bot.send_message(chat_id=MY_ID, text="🤔 <b>Thinking...</b>", parse_mode="HTML")
    full_response, actions_taken = "", []
    async def callback(e_type, e_data):
        nonlocal full_response
        if e_type == "message" and e_data.get("role") == "assistant":
            full_response += e_data.get("content", "")
        elif e_type == "tool_use":
            name = e_data.get("tool_name") or e_data.get("name") or "tool"
            params = e_data.get("parameters", {})
            p_val = params.get("file_path") or params.get("dir_path") or params.get("command") or ""
            actions_taken.append((TOOL_MAPPING.get(name, name), str(p_val)[:20], True))
    await call_gemini_stream(prompt, MY_ID, callback)
    try: await status_msg.delete()
    except: pass
    final_text = _format_html_response(full_response)
    report = _summarize_actions(actions_taken)
    if final_text or report:
        await _send_long_message(GLOBAL_APPLICATION.bot, f"📅 <b>Report</b>\n\n{report}\n\n{final_text}", parse_mode="HTML")


async def chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_not_user(update): return
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        try:
            res = subprocess.check_output(["gemini", "--list-sessions"], text=True)
            await update.message.reply_text(f"<pre>{res}</pre>", parse_mode="HTML")
        except Exception as e: await update.message.reply_text(f"Error: {e}")
    else:
        target = args[0].lower()
        if target == "new":
            set_current_session(chat_id, "fresh_session")
            await update.message.reply_text("✨ <b>New session started!</b>", parse_mode="HTML")
        else:
            try:
                res = subprocess.check_output(["gemini", "--list-sessions"], text=True)
                session_id = None
                for line in res.splitlines():
                    if f"{target}." in line or target in line:
                        match = re.search(r"\[(.*?)\]", line)
                        if match: session_id = match.group(1); break
                if session_id:
                    set_current_session(chat_id, session_id)
                    await update.message.reply_text(f"✅ Switched: <code>{session_id}</code>", parse_mode="HTML")
                else: await update.message.reply_text("❌ Not found.")
            except Exception as e: await update.message.reply_text(f"Error: {e}")
    if chat_id in ACTIVE_STATUS_MSGS: await _refresh_thinking_msg(chat_id)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_not_user(update): return
    chat_id = update.effective_chat.id
    procs = ACTIVE_SUBPROCESSES.get(chat_id, [])
    status = f"⚙️ <b>Status:</b> {len(procs)} active." if procs else "💤 <b>Status:</b> Idle."
    await update.message.reply_text(status, parse_mode="HTML")
    if chat_id in ACTIVE_STATUS_MSGS: await _refresh_thinking_msg(chat_id)


async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive task management."""
    if is_not_user(update): return
    import json
    if not os.path.exists(TASKS_FILE):
        await update.message.reply_text("ℹ️ No tasks scheduled.")
        return
    with open(TASKS_FILE, 'r') as f: tasks = json.load(f)
    if not tasks:
        await update.message.reply_text("ℹ️ Task list is empty.")
        return
    
    text = "📋 <b>Scheduled Tasks:</b>\n\n"
    keyboard = []
    for i, t in enumerate(tasks):
        text += f"{i+1}. <b>{t['name']}</b> at {t['time']}\n"
        keyboard.append([InlineKeyboardButton(f"❌ Delete {t['name']}", callback_data=f"del_task_{i}")])
    
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Self-update gemini-cli."""
    if is_not_user(update): return
    msg = await update.message.reply_text("🔄 <b>Updating Gemini CLI...</b>", parse_mode="HTML")
    try:
        proc = await asyncio.create_subprocess_shell(
            "npm install -g @google/gemini-cli@nightly",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            await msg.edit_text("✅ <b>Gemini CLI updated!</b>\nRestarting bot process...", parse_mode="HTML")
            os.execv(sys.executable, ['python'] + sys.argv)
        else:
            await msg.edit_text(f"❌ <b>Update failed:</b>\n<code>{stderr.decode()}</code>", parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"❌ <b>Error:</b> {e}", parse_mode="HTML")


async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles task deletion buttons."""
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_task_"):
        import json
        idx = int(query.data.split("_")[-1])
        with open(TASKS_FILE, 'r') as f: tasks = json.load(f)
        if 0 <= idx < len(tasks):
            removed = tasks.pop(idx)
            with open(TASKS_FILE, 'w') as f: json.dump(tasks, f, indent=4)
            await query.edit_message_text(f"✅ Task <b>{removed['name']}</b> deleted.", parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or is_not_user(update): return
    chat_id = update.effective_chat.id
    if chat_id not in CHAT_LOCKS: CHAT_LOCKS[chat_id] = asyncio.Lock()
    async with CHAT_LOCKS[chat_id]:
        user_input = await _handle_attachments(update.message)
        STOP_SIGNAL[chat_id] = False
        status_msg = await update.message.reply_text("🤔 <b>Thinking...</b>", parse_mode="HTML")
        ACTIVE_STATUS_MSGS[chat_id] = status_msg
        full_response, actions_taken = "", []
        last_update_time, text_since_last_action = 0, False

        async def callback(e_type, e_data):
            nonlocal full_response, last_update_time, actions_taken, text_since_last_action
            if STOP_SIGNAL.get(chat_id): return
            if e_type == "message" and e_data.get("role") == "assistant":
                content = e_data.get("content", "")
                if content:
                    full_response += content
                    if _format_html_response(content): text_since_last_action = True
                now = asyncio.get_event_loop().time()
                if now - last_update_time > 1.2:
                    clean = _format_html_response(full_response)
                    if clean:
                        disp = clean if len(clean) <= 3500 else "..." + clean[-3500:]
                        try: await ACTIVE_STATUS_MSGS[chat_id].edit_text(f"{disp} ▌", parse_mode="HTML"); last_update_time = now
                        except: pass
            elif e_type == "tool_use":
                name = e_data.get("tool_name") or e_data.get("name") or "tool"
                params = e_data.get("parameters", {})
                p_val = params.get("file_path") or params.get("dir_path") or params.get("command") or params.get("pattern") or ""
                p_disp = (str(p_val)[:25] + "...") if len(str(p_val)) > 25 else str(p_val)
                actions_taken.append((TOOL_MAPPING.get(name, name), p_disp, text_since_last_action))
                text_since_last_action = False
                disp = f"⚙️ <b>Exec:</b> <code>{(p_val[:40] + '...') if len(str(p_val)) > 40 else p_val}</code>" if name == "run_shell_command" else f"⚙️ <i>Using: {name}...</i>"
                try: await ACTIVE_STATUS_MSGS[chat_id].edit_text(disp, parse_mode="HTML")
                except: pass

        exit_code, err = await call_gemini_stream(user_input, chat_id, callback)
        if chat_id in ACTIVE_STATUS_MSGS:
            try: await ACTIVE_STATUS_MSGS[chat_id].delete()
            except: pass
            ACTIVE_STATUS_MSGS.pop(chat_id)
        
        final_text = _format_html_response(full_response)
        report = _summarize_actions(actions_taken)
        full_msg = f"{report}\n\n{final_text}".strip()
        if full_msg: await _send_long_message(update.message, full_msg, parse_mode="HTML")
        elif exit_code != 0: await update.message.reply_text(f"❌ <b>Error:</b> {err[:500]}", parse_mode="HTML")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_not_user(update): return
    chat_id = update.effective_chat.id
    STOP_SIGNAL[chat_id] = True
    procs = ACTIVE_SUBPROCESSES.get(chat_id, [])
    for p in procs:
        try: p.kill()
        except: pass
    await update.message.reply_text("🛑 Stopped.", parse_mode="HTML")
    ACTIVE_STATUS_MSGS.pop(chat_id, None)


async def post_init(application):
    global GLOBAL_APPLICATION
    GLOBAL_APPLICATION = application
    commands = [BotCommand("start", "Menu"), BotCommand("chat", "Sessions"), BotCommand("tasks", "Manage Tasks"), BotCommand("status", "Status"), BotCommand("update", "Update CLI"), BotCommand("stop", "Stop")]
    await application.bot.set_my_commands(commands)
    if hasattr(application, "scheduler_func") and application.scheduler_func:
        asyncio.create_task(application.scheduler_func(trigger_scheduled_task))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler"""
    logger.error(f"Telegram Error: {context.error}")


def run_bot(scheduler_func=None):
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    application.scheduler_func = scheduler_func
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("<b>Ready!</b>", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup([['/chat', '/tasks', '/status'], ['/update', '/stop']], resize_keyboard=True, is_persistent=True)), block=False))
    application.add_handler(CommandHandler("stop", stop_cmd, block=False))
    application.add_handler(CommandHandler("status", status_cmd, block=False))
    application.add_handler(CommandHandler("chat", chat_cmd, block=False))
    application.add_handler(CommandHandler("tasks", tasks_cmd, block=False))
    application.add_handler(CommandHandler("update", update_cmd, block=False))
    application.add_handler(CallbackQueryHandler(task_callback))
    application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message, block=False))
    application.add_error_handler(error_handler)
    application.run_polling()
