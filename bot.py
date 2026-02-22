from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os, time, requests, jwt

load_dotenv()
k = os.getenv("DEEPL_API_KEY", "")
print("DEEPL key len:", len(k), "endswith_fx:", k.endswith(":fx"), flush=True)
app = Flask(__name__)

DOMAIN_ID = os.getenv("DOMAIN_ID", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
SERVICE_ACCOUNT = os.getenv("SERVICE_ACCOUNT", "")
PRIVATE_KEY_FILE = os.getenv("PRIVATE_KEY_FILE", "private.key")

BOT_ID = os.getenv("BOT_ID", "")
BOT_USER_ID = os.getenv("BOT_USER_ID", "")

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
SHOW_ORIGINAL = os.getenv("SHOW_ORIGINAL", "0") == "1"

# ---- LINE WORKS: Access Token取得（Service Account JWT） ----
_cached_token = {"access_token": None, "exp": 0}

def _load_private_key():
    with open(PRIVATE_KEY_FILE, "r", encoding="utf-8") as f:
        return f.read()

def get_lineworks_access_token():
    # キャッシュ（有効なら再利用）
    now = int(time.time())
    if _cached_token["access_token"] and now < _cached_token["exp"] - 30:
        return _cached_token["access_token"]

    private_key = _load_private_key()

    # JWT作成
    iat = now
    exp = now + 60 * 55  # 55分くらい
    payload = {
        "iss": CLIENT_ID,
        "sub": SERVICE_ACCOUNT,
        "iat": iat,
        "exp": exp
    }
    assertion = jwt.encode(payload, private_key, algorithm="RS256")

    # Token API（OAuth2）
    token_url = "https://auth.worksmobile.com/oauth2/v2.0/token"
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "assertion": assertion,
        "scope": "bot bot.message bot.read"
    }
    r = requests.post(token_url, data=data, timeout=15)
    print("token http:", r.status_code, r.text, flush=True)
    r.raise_for_status()
    token = r.json()["access_token"]
    expires_in = int(r.json().get("expires_in", 3600))

    _cached_token["access_token"] = token
    _cached_token["exp"] = now + expires_in
    return token

# ---- 翻訳（DeepL: HU -> JA）※まずは動作確認ならここは後でもOK ----
def translate(text, target_lang):
    if not DEEPL_API_KEY:
        return f"(翻訳API未設定) {text}"

    url = "https://api-free.deepl.com/v2/translate"
    headers = {
        "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "text": [text],          # ← 配列で渡す
        "target_lang": target_lang
        # source_lang を省略＝自動判定
    }

    r = requests.post(url, headers=headers, json=payload, timeout=15)
    print("DEEPL status:", r.status_code, r.text, flush=True)
    r.raise_for_status()

    return r.json()["translations"][0]["text"]

def looks_like_japanese(s: str) -> bool:
    # ひらがな・カタカナ・漢字が1文字でもあれば日本語扱い
    for ch in s:
        code = ord(ch)
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x4E00 <= code <= 0x9FFF):
            return True
    return False

# ---- LINE WORKSへ返信 ----
def reply_to_lineworks(channel_id, message):
    access_token = get_lineworks_access_token()
    url = f"https://www.worksapis.com/v1.0/bots/{BOT_ID}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"content": {"type": "text", "text": message}}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    print("reply http:", r.status_code, r.text, flush=True)
    r.raise_for_status()

# ---- Webhook受信 ----
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("=== webhook data ===", flush=True)
    print(data, flush=True)

    sender_id = data.get("source", {}).get("userId", "")
    channel_id = data.get("source", {}).get("channelId", "")
    text = data.get("content", {}).get("text", "") or ""

    # ---- 強制翻訳コマンド (#JA / #HU) ----
    forced = None
    raw = text.strip()

    if raw.upper().startswith("#JA "):
        forced = "JA"
        raw = raw[4:].strip()
    elif raw.upper().startswith("#HU "):
        forced = "HU"
        raw = raw[4:].strip()

    print("channel_id:", channel_id, "text:", text, flush=True)

    # ループ防止：Bot自身（設定している場合） or Bot投稿タグ
    if BOT_USER_ID and sender_id == BOT_USER_ID:
        return jsonify({"status": "ignored"})
    if text.startswith("[X→]"):
        return jsonify({"status": "ignored"})

    try:
        # ---- 強制指定があれば優先 ----
        if forced:
            translated = translate(raw, forced)
            if forced == "JA":
                body = f"🇯🇵 {translated}"
                if SHOW_ORIGINAL:
                    body = f"🌍 {raw}\n{body}"
            else:
                body = f"🇭🇺 {translated}"
                if SHOW_ORIGINAL:
                    body = f"🇯🇵 {raw}\n{body}"

        # ---- 自動判定 ----
        else:
            if looks_like_japanese(text):
                translated = translate(text, "HU")
                body = f"🇭🇺 {translated}"
                if SHOW_ORIGINAL:
                    body = f"🇯🇵 {text}\n{body}"
            else:
                translated = translate(text, "JA")
                body = f"🇯🇵 {translated}"
                if SHOW_ORIGINAL:
                    body = f"🌍 {text}\n{body}"

        reply_text = "[X→] " + body   # ループ防止タグ
        reply_to_lineworks(channel_id, reply_text)
        print("reply OK", flush=True)

    except Exception as e:
        print("ERROR main:", repr(e), flush=True)
        try:
            reply_to_lineworks(channel_id, "⚠ 翻訳に失敗しました。もう一度送ってください。")
        except Exception as e2:
            print("ERROR fallback:", repr(e2), flush=True)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
