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

    """ 1. Robust thinking formatting """
    # Convert <thinking>...</thinking> to <i>...</i>
    clean_text = re.sub(
        r"<thinking>(.*?)</thinking>", r"<i>\1</i>", text, flags=re.DOTALL
    )
    # Handle unclosed <thinking> for live streaming
    if "<thinking>" in clean_text and "</i>" not in clean_text[clean_text.find("<thinking>"):]:
        clean_text = clean_text.replace("<thinking>", "<i>") + "</i>"
    
    # Remove [Thought: ...] blocks but keep the content if it's not in tags
    clean_text = re.sub(
        r"\[Thought:.*?\]", "", clean_text, flags=re.DOTALL
    )

    clean_text = clean_text.strip()
    if not clean_text:
        return ""

    """ 2. Escape basic HTML to avoid parse errors (except our tags) """
    # We protect our intended tags first
    clean_text = clean_text.replace("<b>", "[[B]]").replace("</b>", "[[/B]]")
    clean_text = clean_text.replace("<i>", "[[I]]").replace("</i>", "[[/I]]")
    clean_text = clean_text.replace("<code>", "[[C]]").replace("</code>", "[[/C]]")
    clean_text = clean_text.replace("<pre>", "[[P]]").replace("</pre>", "[[/P]]")

    clean_text = clean_text.replace("&", "&amp;")
    clean_text = clean_text.replace("<", "&lt;")
    clean_text = clean_text.replace(">", "&gt;")

    # Restore our tags
    clean_text = clean_text.replace("[[B]]", "<b>").replace("[[/B]]", "</b>")
    clean_text = clean_text.replace("[[I]]", "<i>").replace("[[/I]]", "</i>")
    clean_text = clean_text.replace("[[C]]", "<code>").replace("[[/C]]", "</code>")
    clean_text = clean_text.replace("[[P]]", "<pre>").replace("[[/P]]", "</pre>")

    """ 3. Convert Markdown to HTML """
    # Code blocks: ```text``` -> <code>text</code>
    clean_text = re.sub(r"```(?:[\w]+)?\n?(.*?)```", r"<code>\1</code>", clean_text, flags=re.DOTALL)
    # Bold: **text** -> <b>text</b>
    clean_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", clean_text)
    # Italic: *text* -> <i>text</i>
    clean_text = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"<i>\1</i>", clean_text)
    # Inline Code: `text` -> <code>text</code>
    clean_text = re.sub(r"`(.*?)`", r"<code>\1</code>", clean_text)

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
    """Main message handler with live streaming updates and progressive message delivery."""
    if not update.message or is_not_user(update):
        return

    chat_id = update.message.chat_id
    if chat_id not in CHAT_LOCKS:
        CHAT_LOCKS[chat_id] = asyncio.Lock()

    async with CHAT_LOCKS[chat_id]:
        user_input = await _handle_attachments(update.message)
        STOP_SIGNAL[chat_id] = False

        # Initial thinking message is silent
        status_msg = await update.message.reply_text("🤔 <b>Thinking...</b>", parse_mode="HTML", disable_notification=True)
        
        # current_buffer holds the text for the current status_msg
        current_buffer = ""
        last_update_time = 0
        
        async def finalize_current_msg(is_final=False):
            """Edits the current status_msg one last time and resets the buffer."""
            nonlocal status_msg, current_buffer
            if not current_buffer:
                return
            
            clean = _format_html_response(current_buffer)
            if clean:
                try:
                    # Editing a message never triggers a notification anyway
                    await status_msg.edit_text(clean, parse_mode="HTML")
                except:
                    pass
            current_buffer = ""

        async def callback(event_type, event_data):
            nonlocal current_buffer, status_msg, last_update_time
            if STOP_SIGNAL.get(chat_id): return
            
            if event_type == "message" and event_data.get("role") == "assistant":
                new_content = event_data.get("content", "")
                current_buffer += new_content
                
                now = asyncio.get_event_loop().time()
                # Periodic live update
                if now - last_update_time > 1.0:
                    clean = _format_html_response(current_buffer)
                    if clean:
                        # Truncate for live display if getting very long
                        display_text = clean
                        if len(display_text) > 3500:
                            # If it's too long, finalize it and start a new message (silent)
                            await finalize_current_msg()
                            status_msg = await update.message.reply_text("<i>...continuing...</i>", parse_mode="HTML", disable_notification=True)
                            return

                        try:
                            await status_msg.edit_text(f"{display_text} ▌", parse_mode="HTML")
                            last_update_time = now
                        except: pass
            
            elif event_type == "tool_use":
                # Finalize any text before the tool
                await finalize_current_msg()
                
                name = event_data.get("tool_name") or event_data.get("name") or "tool"
                # Send a NEW message for the tool usage (silent)
                status_msg = await update.message.reply_text(f"⚙️ <i>Using: {name}...</i>", parse_mode="HTML", disable_notification=True)
                last_update_time = 0
                
            elif event_type == "tool_result":
                # Update the tool message to show completion (silent)
                try:
                    await status_msg.edit_text("✅ <i>Tool execution completed.</i>", parse_mode="HTML")
                except: pass
                # Prepare for the next message (silent)
                status_msg = await update.message.reply_text("🤔 <b>Analyzing result...</b>", parse_mode="HTML", disable_notification=True)
                last_update_time = 0

        exit_code, error_msg = await call_gemini_stream(user_input, chat_id, callback)

        if STOP_SIGNAL.get(chat_id):
            try: await status_msg.edit_text("🛑 <b>Stopped.</b>", parse_mode="HTML")
            except: pass
            return

        # Finalize the last remaining part of the response. 
        # Note: If we want the VERY last message to notify, we'd need to send a NEW message instead of editing.
        # But Telegram doesn't notify on edits. 
        # To ensure a notification at the end, we can send a small "Done" message if the response was multi-part.
        if current_buffer:
            await finalize_current_msg()
            # If the process was long and involved multiple messages, send a tiny ping
            # await update.message.reply_text("✅", disable_notification=False)
        else:
            try:
                if "Thinking..." in status_msg.text or "Analyzing" in status_msg.text:
                    await status_msg.delete()
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
