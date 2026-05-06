import os
import time
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN missing", flush=True)
    raise SystemExit(1)

BASE = f"https://api.telegram.org/bot{TOKEN}"
LAST_UPDATE_ID = 0


def tg(method, payload=None):
    try:
        r = requests.post(f"{BASE}/{method}", json=payload or {}, timeout=20)
        print(f"TG {method}: {r.status_code} {r.text[:500]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"TG ERROR {method}: {e}", flush=True)
        return None


def send(chat_id, text):
    tg("sendMessage", {
        "chat_id": chat_id,
        "text": text
    })


def main():
    global LAST_UPDATE_ID

    print("Telegram test bot starting...", flush=True)

    tg("deleteWebhook", {"drop_pending_updates": False})
    me = tg("getMe")

    if not me or not me.get("ok"):
        print("ERROR: token invalid or Telegram API failed", flush=True)
        return

    print("Telegram token OK. Waiting for messages...", flush=True)

    while True:
        try:
            params = {
                "offset": LAST_UPDATE_ID + 1,
                "timeout": 10,
                "limit": 20,
            }

            r = requests.get(f"{BASE}/getUpdates", params=params, timeout=15)
            data = r.json()

            print(f"getUpdates: {data}", flush=True)

            for update in data.get("result", []):
                LAST_UPDATE_ID = update["update_id"]

                msg = update.get("message", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = msg.get("text", "")

                if chat_id:
                    send(chat_id, f"✅ Telegram test working!\nYou sent: {text}")

        except Exception as e:
            print(f"LOOP ERROR: {e}", flush=True)

        time.sleep(2)


if __name__ == "__main__":
    main()
