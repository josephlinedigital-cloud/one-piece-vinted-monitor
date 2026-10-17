"""One-time helper: discover your Telegram chat ID without sharing the bot token in chat."""
import json
import os
import sys
import urllib.request


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in your own terminal first (see README.md).")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = json.loads(response.read())
    except Exception as error:
        sys.exit(f"Could not contact Telegram: {type(error).__name__}")
    if not body.get("ok"):
        sys.exit("Telegram rejected your bot credentials.")
    ids = {}
    for update in body.get("result", []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            ids[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name") or "Chat"
    if not ids:
        print("No chats found. Open your bot in Telegram, press Start, send 'hello', then run again.")
    else:
        for chat_id, name in ids.items():
            print(f"Chat: {name} | TELEGRAM_CHAT_ID: {chat_id}")


if __name__ == "__main__":
    main()
