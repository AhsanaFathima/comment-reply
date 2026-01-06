import os
import re
import hmac
import hashlib
import base64
from datetime import datetime
from flask import Flask, request, jsonify, abort
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# ---------------- INIT ----------------
load_dotenv()
app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")

slack = WebClient(token=SLACK_BOT_TOKEN)

ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b", re.IGNORECASE)
processed = set()

print("🚀 Shopify GraphQL → Slack bridge started", flush=True)

# ---------------- VERIFY WEBHOOK ----------------
def verify_shopify(raw_body, hmac_header):
    if not SHOPIFY_WEBHOOK_SECRET:
        return True

    if not hmac_header:
        return False

    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256
    ).digest()

    return hmac.compare_digest(digest, base64.b64decode(hmac_header))


# ---------------- SLACK ----------------
def find_thread(order_number):
    search = f"st.order #{order_number}"
    try:
        res = slack.conversations_history(
            channel=SLACK_CHANNEL_ID,
            limit=200
        )
        for msg in res["messages"]:
            if search in msg.get("text", "").lower():
                return msg["ts"]
    except SlackApiError as e:
        print("Slack search error:", e.response["error"])
    return None


def reply_thread(ts, text, author):
    slack.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        thread_ts=ts,
        text=text,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*💬 {author}*\n{text}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_From Shopify • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
                    }
                ]
            }
        ]
    )


# ---------------- ROUTES ----------------
@app.route("/")
def health():
    return {"status": "ok", "mode": "graphql"}


@app.route("/webhook/shopify", methods=["POST"])
def webhook():
    print("🔥 GraphQL webhook HIT", flush=True)

    if not verify_shopify(
        request.data,
        request.headers.get("X-Shopify-Hmac-Sha256")
    ):
        abort(401)

    payload = request.get_json(silent=True) or {}

    # -------- GRAPHQL COMMENT PAYLOAD --------
    comment = payload.get("comment", {})
    comment_text = comment.get("message")
    author = comment.get("author", {}).get("name", "Shopify")

    if not comment_text:
        return {"status": "no_comment"}, 200

    match = ORDER_REGEX.search(comment_text)
    if not match:
        return {"status": "no_pattern"}, 200

    order_number = match.group(1)

    fingerprint = f"{order_number}:{comment_text}"
    if fingerprint in processed:
        return {"status": "duplicate"}, 200
    processed.add(fingerprint)

    print(f"📦 Comment matched ST.order #{order_number}", flush=True)

    ts = find_thread(order_number)

    if ts:
        reply_thread(ts, comment_text, author)
        return {"status": "posted_in_thread"}, 200

    slack.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=f"💬 *Comment on ST.order #{order_number}*\n{comment_text}\n_(Thread not found)_"
    )

    return {"status": "posted_as_new"}, 200


# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
