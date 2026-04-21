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
    """
    Cleans AI tags and converts Markdown to HTML for Telegram.
    Telegram HTML supports: <b>, <i>, <code>, <s>, <u>, <pre>.
    """
    if not text:
        return ""

    """ 1. Robust thinking removal """
    # Remove <thinking>...</thinking>
    clean_text = re.sub(
        r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL
    )
    # Remove any unclosed <thinking>
    clean_text = re.sub(
        r"<thinking>.*$", "", clean_text, flags=re.DOTALL
    )
    # Remove [Thought: ...] blocks and the reasoning immediately following it
    clean_text = re.sub(
        r"\[Thought:.*?\]", "", clean_text, flags=re.DOTALL
    )
    
    # Remove common reasoning headers at the start of paragraphs
    headers_to_strip = [
        "Considering", "Analyzing", "Summarizing", "Thinking", 
        "Refining", "Investigating", "Checking", "Evaluating",
        "Determining", "Scrutinizing", "Updating", "Developing"
    ]
    for header in headers_to_strip:
        clean_text = re.sub(rf"^\s*{header}.*?\n", "", clean_text, flags=re.MULTILINE)
        clean_text = re.sub(rf"^\s*\*\*{header}.*?\*\*.*?\n", "", clean_text, flags=re.MULTILINE)

    clean_text = clean_text.strip()
    if not clean_text:
        return ""

    """ 2. Escape basic HTML to avoid parse errors """
    clean_text = clean_text.replace("&", "&amp;")
    clean_text = clean_text.replace("<", "&lt;")
    clean_text = clean_text.replace(">", "&gt;")

    """ 3. Convert Markdown to HTML """
    # Code blocks: ```text``` -> <code>text</code>
    clean_text = re.sub(r"```(?:[\w]+)?\n?(.*?)```", r"<code>\1</code>", clean_text, flags=re.DOTALL)
    # Bold: **text** -> <b>text</b>
    clean_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", clean_text)
    # Italic: *text* -> <i>text</i>
    clean_text = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"<i>\1</i>", clean_text)
    # Inline Code: `text` -> <code>text</code>
    clean_text = re.sub(r"`(.*?)`", r"<code>\1</code>", clean_text)

    """ 4. Restore tags """
    replacements = {
        "&lt;b&gt;": "<b>", "&lt;/b&gt;": "</b>",
        "&lt;i&gt;": "<i>", "&lt;/i&gt;": "</i>",
        "&lt;code&gt;": "<code>", "&lt;/code&gt;": "</code>",
        "&lt;pre&gt;": "<pre>", "&lt;/pre&gt;": "</pre>"
    }
    for old, new in replacements.items():
        clean_text = clean_text.replace(old, new)

    return clean_text


async def _send_long_message(message_obj, text: str, **kwargs):
    """Sends a long message by splitting it into chunks safely."""
    if not text:
        return

    limit = 4000
    while len(text) > 0:
        if len(text) <= limit:
            chunk = text
            text = ""
        else:
            split_at = text.rfind("\n", 0, limit)
            if split_at == -1:
                split_at = text.rfind(" ", 0, limit)
            if split_at == -1:
                split_at = limit
            
            chunk = text[:split_at]
            text = text[split_at:].lstrip()

        try:
            # message_obj can be a Message or the bot itself with chat_id
            if hasattr(message_obj, "reply_text"):
                await message_obj.reply_text(chunk, **kwargs)
            else:
                await message_obj.send_message(chat_id=MY_ID, text=chunk, **kwargs)
        except Exception as e:
            logger.error(f"Error sending message chunk: {e}")
            try:
                if hasattr(message_obj, "reply_text"):
                    await message_obj.reply_text(chunk)
                else:
                    await message_obj.send_message(chat_id=MY_ID, text=chunk)
            except: pass


async def trigger_scheduled_task(prompt: str):
    """Called by the scheduler to run an agent task automatically."""
    if not GLOBAL_APPLICATION:
        return

    logger.info(f"Executing scheduled prompt: {prompt[:50]}...")
    await GLOBAL_APPLICATION.bot.send_message(chat_id=MY_ID, text="📅 <b>Scheduled Task Triggered</b>", parse_mode="HTML")
    
    full_response = ""
    async def callback(event_type, event_data):
        nonlocal full_response
        if event_type == "message" and event_data.get("role") == "assistant":
            full_response += event_data.get("content", "")

    await call_gemini_stream(prompt, MY_ID, callback)
    
    final_text = _format_html_response(full_response)
    if final_text:
        await _send_long_message(GLOBAL_APPLICATION.bot, f"📅 <b>Scheduled Report</b>\n\n{final_text}", parse_mode="HTML")


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
    """Main message handler with live streaming updates."""
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
                        # Truncate live updates to avoid length errors
                        display_text = clean
                        if len(display_text) > 3500:
                            display_text = "..." + display_text[-3500:]
                        try:
                            await status_msg.edit_text(f"{display_text} ▌", parse_mode="HTML")
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
            try:
                if len(final_text) < 4000:
                    await status_msg.edit_text(final_text, parse_mode="HTML")
                else:
                    await status_msg.delete()
                    await _send_long_message(update.message, final_text, parse_mode="HTML")
            except Exception as e:
                await _send_long_message(update.message, final_text, parse_mode="HTML")
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
    
    if hasattr(application, "scheduler_func") and application.scheduler_func:
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
