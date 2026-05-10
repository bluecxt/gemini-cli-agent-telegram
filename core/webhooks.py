"""
Webhook Server - Receives external triggers (GitHub, Anilist, etc.)
and triggers the Gemini Agent.
"""

import asyncio
import json
import os
from aiohttp import web
from .logger import logger
from .telegram_handler import trigger_scheduled_task

async def handle_github(request):
    """Handles GitHub Webhook events."""
    try:
        payload = await request.json()
        event = request.headers.get("X-GitHub-Event", "push")
        
        if event == "push":
            repo_name = payload.get("repository", {}).get("full_name", "Unknown")
            commit_msg = payload.get("head_commit", {}).get("message", "No message")
            author = payload.get("head_commit", {}).get("author", {}).get("name", "Unknown")
            
            prompt = (
                f"L'utilisateur {author} vient de commit sur GitHub dans le repo {repo_name}.\n"
                f"Message du commit : {commit_msg}\n"
                f"Analyse ce commit et dis-moi si tout semble correct ou si tu as des suggestions."
            )
            asyncio.create_task(trigger_scheduled_task(prompt))
            return web.Response(text="GitHub Push received and agent triggered.")

        elif event == "issues":
            action = payload.get("action")
            repo_name = payload.get("repository", {}).get("full_name", "Unknown")
            issue = payload.get("issue", {})
            title = issue.get("title")
            body = issue.get("body", "No description")
            author = issue.get("user", {}).get("login", "Unknown")
            
            if action == "opened":
                prompt = (
                    f"Une nouvelle issue a été ouverte par {author} dans le repo {repo_name}.\n"
                    f"Titre : {title}\n"
                    f"Description : {body}\n"
                    f"Explique-moi ce problème en détail et propose une piste de solution si possible."
                )
                asyncio.create_task(trigger_scheduled_task(prompt))
                return web.Response(text="GitHub Issue received and agent triggered.")
            
    except Exception as e:
        logger.error(f"Webhook GitHub Error: {e}")
        return web.Response(text="Error", status=500)
    
    return web.Response(text="OK")

async def handle_custom_trigger(request):
    """Generic trigger for any other service."""
    try:
        payload = await request.json()
        prompt = payload.get("prompt")
        if prompt:
            asyncio.create_task(trigger_scheduled_task(prompt))
            return web.Response(text="Agent triggered.")
    except Exception as e:
        logger.error(f"Custom Webhook Error: {e}")
    return web.Response(text="Invalid payload", status=400)

async def start_webhook_server(port=8080):
    """Starts the aiohttp server."""
    app = web.Application()
    app.router.add_post('/webhooks/github', handle_github)
    app.router.add_post('/webhooks/trigger', handle_custom_trigger)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    logger.info(f"Webhook server starting on port {port}...")
    await site.start()
