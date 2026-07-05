from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import time
import requests
import jwt

load_dotenv()
app = Flask(__name__)

# =========================
# 基本設定
# =========================
DOMAIN_ID = os.getenv("DOMAIN_ID", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
SERVICE_ACCOUNT = os.getenv("SERVICE_ACCOUNT", "")

BOT_ID = os.getenv("BOT_ID", "")
BOT_USER_ID = os.getenv("BOT_USER_ID", "")

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
SHOW_ORIGINAL = os.getenv("SHOW_ORIGINAL", "0") == "1"

PRIVATE_KEY_FILE = os.getenv("PRIVATE_KEY_FILE", "private.key")

TEST_MODE = os.getenv("TEST_MODE", "0") == "1"
ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID", "")

# =========================
# Ver.2.0 チャンネル別翻訳先
# 日本語 → 各チャンネルの言語
# 外国語 → 日本語
# =========================
CHANNEL_LANG = {
    # Hungarian Language
    "ae234ac8-0ba3-61b3-6267-0c0da0d09e40": "HU",

    # English Language
    "e3de398b-340a-0e03-0d36-06c0845c31be": "EN-US",

    # 今後追加予定
    # "German channel_id": "DE",
    # "French channel_id": "FR",
    # "Italian channel_id": "IT",
}

DEFAULT_LANG = "HU"

print("DEEPL key len:", len(DEEPL_API_KEY), "endswith_fx:", DEEPL_API_KEY.endswith(":fx"), flush=True)
print("CHANNEL_LANG:", CHANNEL_LANG, flush=True)

CHANNEL_NAME = {
    "ae234ac8-0ba3-61b3-6267-0c0da0d09e40": "Hungarian Language",
    "e3de398b-340a-0e03-0d36-06c0845c31be": "English Language",
    "59858f7c-b490-c9ba-c297-2795d7f76bdd": "Admin Channel",
}

# =========================
# Render ヘルスチェック
# =========================
@app.get("/")
def health():
    return "ok", 200


# =========================
# 秘密鍵読み込み
# Render: PRIVATE_KEY_PEM
# Local : private.key
# =========================
def load_private_key():
    pem = os.getenv("PRIVATE_KEY_PEM", "")
    if pem.strip():
        return pem.replace("\\n", "\n")

    with open(PRIVATE_KEY_FILE, "r", encoding="utf-8") as f:
        return f.read()


# =========================
# LINE WORKS Access Token
# =========================
_cached_token = {
    "access_token": None,
    "exp": 0,
}


def get_lineworks_access_token():
    now = int(time.time())

    if _cached_token["access_token"] and now < _cached_token["exp"] - 30:
        return _cached_token["access_token"]

    private_key = load_private_key()

    payload = {
        "iss": CLIENT_ID,
        "sub": SERVICE_ACCOUNT,
        "iat": now,
        "exp": now + 60 * 55,
    }

    assertion = jwt.encode(payload, private_key, algorithm="RS256")

    token_url = "https://auth.worksmobile.com/oauth2/v2.0/token"

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "assertion": assertion,
        "scope": "bot bot.message bot.read",
    }

    r = requests.post(token_url, data=data, timeout=15)

    # トークン本文はログに出さない
    print("token http:", r.status_code, flush=True)

    r.raise_for_status()

    js = r.json()
    _cached_token["access_token"] = js["access_token"]
    _cached_token["exp"] = now + int(js.get("expires_in", 3600))

    return _cached_token["access_token"]


# =========================
# DeepL 翻訳
# =========================
def translate(text, target_lang):
    if not DEEPL_API_KEY:
        return f"(翻訳API未設定) {text}"

    url = "https://api-free.deepl.com/v2/translate"

    headers = {
        "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": [text],
        "target_lang": target_lang,
        # source_lang は省略＝DeepL自動判定
    }

    r = requests.post(url, headers=headers, json=payload, timeout=15)
    print("DEEPL status:", r.status_code, r.text, flush=True)

    r.raise_for_status()

    return r.json()["translations"][0]["text"]


# =========================
# 日本語判定
# =========================
def looks_like_japanese(text):
    for ch in text:
        code = ord(ch)
        if (
            0x3040 <= code <= 0x309F  # ひらがな
            or 0x30A0 <= code <= 0x30FF  # カタカナ
            or 0x4E00 <= code <= 0x9FFF  # 漢字
        ):
            return True
    return False


# =========================
# チャンネル別翻訳先
# =========================
def get_target_lang(channel_id):
    return CHANNEL_LANG.get(channel_id, DEFAULT_LANG)


# =========================
# LINE WORKS 返信
# =========================
def reply_to_lineworks(channel_id, message):
    access_token = get_lineworks_access_token()

    url = f"https://www.worksapis.com/v1.0/bots/{BOT_ID}/channels/{channel_id}/messages"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "content": {
            "type": "text",
            "text": message,
        }
    }

    r = requests.post(url, headers=headers, json=payload, timeout=15)
    print("reply http:", r.status_code, r.text, flush=True)

    r.raise_for_status()

def send_translation(channel_id, message):
    if TEST_MODE:
        if not ADMIN_CHANNEL_ID:
            print("TEST_MODE is ON but ADMIN_CHANNEL_ID is empty", flush=True)
            return

        channel_name = CHANNEL_NAME.get(channel_id, "Unknown Channel")

        admin_message = (
            "[TEST MODE]\n"
            f"channel: {channel_name}\n"
            f"channel_id: {channel_id}\n\n"
            f"{message}"
        )

        reply_to_lineworks(ADMIN_CHANNEL_ID, admin_message)
    else:
        reply_to_lineworks(channel_id, message)

# =========================
# Webhook
# =========================
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "webhook ok", 200

    data = request.json or {}

    print("=== webhook data ===", flush=True)
    print(data, flush=True)

    sender_id = data.get("source", {}).get("userId", "")
    channel_id = data.get("source", {}).get("channelId", "")
    text = data.get("content", {}).get("text", "") or ""
    text = text.strip()

    print("channel_id:", channel_id, "text:", text, flush=True)

    # Bot自身またはBot投稿を無視
    if BOT_USER_ID and sender_id == BOT_USER_ID:
        return jsonify({"status": "ignored"})

    if text.startswith("[X→]"):
        return jsonify({"status": "ignored"})

    try:
        # =========================
        # 強制翻訳コマンド
        # #JA こんにちは
        # #HU こんにちは
        # #EN こんにちは
        # #DE こんにちは
        # #FR こんにちは
        # #IT こんにちは
        # =========================
        forced = None
        raw = text

        if raw.startswith("#") and len(raw) >= 4 and raw[3] == " ":
            code = raw[1:3].upper()
            if code in ["JA", "HU", "EN", "DE", "FR", "IT"]:
                forced = code
                raw = raw[4:].strip()

        if forced:
            target_lang = "EN-US" if forced == "EN" else forced
            translated = translate(raw, target_lang)

            body = translated
            if SHOW_ORIGINAL:
                body = f"{raw}\n{body}"

        else:
            # =========================
            # 自動判定
            # 日本語 → チャンネル別言語
            # 外国語 → 日本語
            # =========================
            if looks_like_japanese(text):
                target_lang = get_target_lang(channel_id)
                translated = translate(text, target_lang)

                body = translated
                if SHOW_ORIGINAL:
                    body = f"{text}\n{body}"

            else:
                translated = translate(text, "JA")

                body = translated
                if SHOW_ORIGINAL:
                    body = f"{text}\n{body}"

        reply_text = "[X→] " + body

        send_translation(channel_id, reply_text)

        print("reply OK", flush=True)

    except Exception as e:
        print("ERROR main:", repr(e), flush=True)

        try:
            send_translation(channel_id, "⚠ 翻訳に失敗しました。もう一度送ってください。")
        except Exception as e2:
            print("ERROR fallback:", repr(e2), flush=True)

    return jsonify({"status": "ok"})


# =========================
# Local 起動用
# Renderでは gunicorn が起動
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)