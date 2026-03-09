"""
Run this script ONCE to create the Telegram session file.
It will send a code to your Telegram app — enter it when prompted.
After running, a 'telegram.session' file will be created.
Never commit this file to Git.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from telethon.sync import TelegramClient

api_id = int(os.getenv('TELEGRAM_API_ID', '0'))
api_hash = os.getenv('TELEGRAM_API_HASH', '')

if not api_id or not api_hash:
    print("ERROR: TELEGRAM_API_ID or TELEGRAM_API_HASH not set in .env")
    sys.exit(1)

session_path = os.path.join(os.path.dirname(__file__), 'telegram')
print(f"Creating session at: {session_path}.session")
print("You will receive a code in your Telegram app. Enter it below.")

with TelegramClient(session_path, api_id, api_hash) as client:
    print("Session created successfully.")
    me = client.get_me()
    print(f"Logged in as: {me.first_name} ({me.phone})")