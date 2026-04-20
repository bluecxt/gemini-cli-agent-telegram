"""
Configuration - Handles environment variables and project constants.
"""

import os
from dotenv import load_dotenv

""" Load variables from .env file explicitly """
load_dotenv()

""" Telegram Settings - Using your .env variable names """
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
MY_ID_RAW = os.getenv("ADMIN_ID", "0").strip()
MY_ID = int(MY_ID_RAW) if MY_ID_RAW.isdigit() else 0

""" Other Tokens """
GITHUB_TOKEN = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()

""" Paths """
ROOT_DIR = os.getcwd()
WORKSPACE_DIR = os.path.join(ROOT_DIR, "workspace")
TMP_DIR = os.path.join(ROOT_DIR, "tmp")
REPOS_DIR = os.path.join(ROOT_DIR, "repos")
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")

""" Ensure directories exist """
for path in [WORKSPACE_DIR, TMP_DIR, REPOS_DIR, SKILLS_DIR]:
    os.makedirs(path, exist_ok=True)

""" Security check """
if not TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN is empty! Check your .env file.")
elif not MY_ID:
    print("❌ ERROR: ADMIN_ID is missing or 0!")
else:
    print(f"✅ Configuration loaded (Bot: {TOKEN[:5]}... | Admin: {MY_ID})")
