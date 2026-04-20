"""
Telegram Handler - Manages user interaction and Gemini streaming.
"""

import asyncio
import os
import re
import subprocess
from telegram import (
    Update,
    Message,
    ReplyKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes
)

from .config import TOKEN, MY_ID, TMP_DIR
from .memory import set_current_session
from .engine import call_gemini_stream, ACTIVE_SUBPROCESSES, STOP_SIGNAL
from .logger import logger

CHAT_LOCKS = {}
GLOBAL_APPLICATION = None


def is_not_user(update: Update) -> bool:
    """Checks if the message is from the authorized user."""
    user_id = update.message.from_user.id
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
    """Cleans AI tags and converts Markdown to HTML for Telegram."""
    if not text:
        return ""

    """ Robust thinking removal """
    clean_text = re.sub(
        r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL
    )
    clean_text = re.sub(
        r"<thinking>.*$", "", clean_text, flags=re.DOTALL
    )
    clean_text = re.sub(
        r"\[Thought:.*?\]", "", clean_text, flags=re.DOTALL
    ).strip()

    if not clean_text:
        return ""

    """ Escape HTML and restore allowed tags """
    clean_text = clean_text.replace("&", "&amp;")
    clean_text = clean_text.replace("<", "&lt;").replace(">", "&gt;")

    # Markdown conversion
    clean_text = re.sub(r"```(?:[\w]+)?\n?(.*?)```", r"<code>\1</code>", clean_text, flags=re.DOTALL)
    clean_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", clean_text)
    clean_text = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"<i>\1</i>", clean_text)
    clean_text = re.sub(r"`(.*?)`", r"<code>\1</code>", clean_text)

    replacements = {
        "&lt;b&gt;": "<b>", "&lt;/b&gt;": "</b>",
        "&lt;i&gt;": "<i>", "&lt;/i&gt;": "</i>",
        "&lt;code&gt;": "<code>", "&lt;/code&gt;": "</code>",
        "&lt;pre&gt;": "<pre>", "&lt;/pre&gt;": "</pre>"
    }
    for old, new in replacements.items():
        clean_text = clean_text.replace(old, new)

    return clean_text


async def trigger_scheduled_task(prompt: str):
    """
    Called by the scheduler to run an agent task automatically.
    """
    if not GLOBAL_APPLICATION:
        return

    logger.info(f"Executing scheduled prompt: {prompt[:50]}...")
    
    # Send an initial notification
    header = "📅 <b>Scheduled Task Triggered</b>"
    msg = await GLOBAL_APPLICATION.bot.send_message(chat_id=MY_ID, text=header, parse_mode="HTML")
    
    full_response = ""
    
    async def callback(event_type, event_data):
        nonlocal full_response
        if event_type == "message" and event_data.get("role") == "assistant":
            full_response += event_data.get("content", "")

    # We use a dummy chat_id for subprocess tracking (or use MY_ID)
    await call_gemini_stream(prompt, MY_ID, callback)
    
    final_text = f"📅 <b>Scheduled Report</b>\n\n{_format_html_response(full_response)}"
    await GLOBAL_APPLICATION.bot.send_message(chat_id=MY_ID, text=final_text[:4096], parse_mode="HTML")


async def chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /chat command."""
    if is_not_user(update):
        return

    chat_id = update.message.chat_id
    args = context.args

    if not args:
        try:
            res = subprocess.check_output(["gemini", "--list-sessions"], text=True)
            await update.message.reply_text(f"<pre>{res}</pre>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    target = args[0].lower()
    if target == "new":
        set_current_session(chat_id, "fresh_session")
        await update.message.reply_text("✨ <b>New session started!</b>", parse_mode="HTML")
        return

    try:
        res = subprocess.check_output(["gemini", "--list-sessions"], text=True)
        lines = res.splitlines()
        session_id = None
        for line in lines:
            if f"{target}." in line or target in line:
                match = re.search(r"\[(.*?)\]", line)
                if match:
                    session_id = match.group(1)
                    break
        if session_id:
            set_current_session(chat_id, session_id)
            await update.message.reply_text(f"✅ Switched: <code>{session_id}</code>", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Not found.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /status command."""
    if is_not_user(update):
        return
    chat_id = update.message.chat_id
    procs = ACTIVE_SUBPROCESSES.get(chat_id, [])
    status = f"⚙️ <b>Status:</b> {len(procs)} active." if procs else "💤 <b>Status:</b> Idle."
    await update.message.reply_text(status, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler."""
    if not update.message or is_not_user(update):
        return

    chat_id = update.message.chat_id
    if chat_id not in CHAT_LOCKS:
        CHAT_LOCKS[chat_id] = asyncio.Lock()

    async with CHAT_LOCKS[chat_id]:
        user_input = await _handle_attachments(update.message)
        STOP_SIGNAL[chat_id] = False

        status_msg = await update.message.reply_text("🤔 <b>Thinking...</b>", parse_mode="HTML")
        full_response = ""
        last_update_time = 0

        async def callback(event_type, event_data):
            nonlocal full_response, status_msg, last_update_time
            if STOP_SIGNAL.get(chat_id): return
            if event_type == "message" and event_data.get("role") == "assistant":
                full_response += event_data.get("content", "")
                now = asyncio.get_event_loop().time()
                if now - last_update_time > 1.2:
                    clean = _format_html_response(full_response)
                    if clean:
                        try:
                            await status_msg.edit_text(f"{clean} ▌", parse_mode="HTML")
                            last_update_time = now
                        except: pass
            elif event_type == "tool_use":
                name = event_data.get("tool_name") or event_data.get("name") or "tool"
                try: await status_msg.edit_text(f"⚙️ <i>Using: {name}...</i>", parse_mode="HTML")
                except: pass

        exit_code, error_msg = await call_gemini_stream(user_input, chat_id, callback)

        if STOP_SIGNAL.get(chat_id):
            try: await status_msg.edit_text("🛑 <b>Stopped.</b>", parse_mode="HTML")
            except: pass
            return

        final_text = _format_html_response(full_response)
        if final_text:
            try: await status_msg.edit_text(final_text, parse_mode="HTML")
            except: await update.message.reply_text(final_text, parse_mode="HTML")
        else:
            try: await status_msg.edit_text("✅ <i>Done</i>", parse_mode="HTML")
            except: pass


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_not_user(update): return
    chat_id = update.message.chat_id
    STOP_SIGNAL[chat_id] = True
    procs = ACTIVE_SUBPROCESSES.get(chat_id, [])
    for p in procs:
        try: p.kill()
        except: pass
    await update.message.reply_text("🛑 Stopped.", parse_mode="HTML")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Telegram Error: {context.error}")


async def post_init(application):
    global GLOBAL_APPLICATION
    GLOBAL_APPLICATION = application
    
    commands = [
        BotCommand("start", "Menu"),
        BotCommand("chat", "Sessions"),
        BotCommand("status", "Status"),
        BotCommand("stop", "Stop"),
    ]
    await application.bot.set_my_commands(commands)
    
    # Start the scheduler task
    if hasattr(application, "scheduler_func"):
        asyncio.create_task(application.scheduler_func(trigger_scheduled_task))


def run_bot(scheduler_func=None):
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    application.scheduler_func = scheduler_func

    application.add_handler(CommandHandler("start", start_cmd, block=False))
    application.add_handler(CommandHandler("stop", stop_cmd, block=False))
    application.add_handler(CommandHandler("status", status_cmd, block=False))
    application.add_handler(CommandHandler("chat", chat_cmd, block=False))
    application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message, block=False))
    application.add_error_handler(error_handler)
    application.run_polling()
