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

slack_client = WebClient(token=SLACK_BOT_TOKEN)

# STRICT MATCH: ONLY "ST.order #1234"
ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b", re.IGNORECASE)

# Deduplication store (in-memory)
processed_comments = set()

print("🚀 Shopify → Slack bridge started", flush=True)

# ---------------- HELPERS ----------------
def verify_shopify_webhook(raw_body, hmac_header):
    """
    Verify Shopify webhook using Base64 HMAC.
    Safe against missing/invalid headers.
    """
    if not SHOPIFY_WEBHOOK_SECRET:
        print("⚠️ SHOPIFY_WEBHOOK_SECRET not set — skipping verification")
        return True

    if not hmac_header:
        print("❌ Missing X-Shopify-Hmac-Sha256 header")
        return False

    try:
        calculated_hmac = hmac.new(
            SHOPIFY_WEBHOOK_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).digest()

        received_hmac = base64.b64decode(hmac_header)

        return hmac.compare_digest(calculated_hmac, received_hmac)

    except Exception as e:
        print("❌ HMAC verification error:", e)
        return False


def find_slack_thread(order_number):
    """Find Slack message containing exact ST.order #XXXX"""
    search_text = f"st.order #{order_number}"

    try:
        response = slack_client.conversations_history(
            channel=SLACK_CHANNEL_ID,
            limit=200
        )

        for msg in response.get("messages", []):
            if search_text in msg.get("text", "").lower():
                return msg["ts"]

    except SlackApiError as e:
        print("❌ Slack search error:", e.response["error"], flush=True)

    return None


def post_slack_thread_reply(thread_ts, comment_text, author="Shopify"):
    """Post reply inside Slack thread"""
    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            thread_ts=thread_ts,
            text=f"💬 {comment_text}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*💬 {author}*\n{comment_text}"
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
        print("✅ Slack thread reply posted", flush=True)

    except SlackApiError as e:
        print("❌ Slack post error:", e.response["error"], flush=True)


# ---------------- ROUTES ----------------
@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "Shopify Comment → Slack Thread"
    })


@app.route("/webhook/shopify", methods=["POST"])
def shopify_webhook():
    print("🔥 /webhook/shopify HIT", flush=True)

    # ---- Verify webhook ----
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")
    if not verify_shopify_webhook(request.data, hmac_header):
        abort(401, "Invalid webhook signature")

    payload = request.get_json(silent=True) or {}
    print("🔔 Shopify webhook received", flush=True)

    comment_text = None

    # ---- Extract comment from order ----
    if "order" in payload:
        order = payload["order"]
        for note in order.get("note_attributes", []):
            if note.get("name") == "note" and note.get("value"):
                comment_text = note["value"]
                break
    else:
        return jsonify({"status": "ignored"}), 200

    # ---- Validate comment ----
    if not comment_text:
        return jsonify({"status": "no_comment"}), 200

    match = ORDER_REGEX.search(comment_text)
    if not match:
        return jsonify({"status": "no_pattern"}), 200

    order_number = match.group(1)

    # ---- Deduplication ----
    fingerprint = f"{order_number}:{comment_text.strip()}"
    if fingerprint in processed_comments:
        print("⏭️ Duplicate comment ignored", flush=True)
        return jsonify({"status": "duplicate"}), 200

    processed_comments.add(fingerprint)

    print(f"📦 Matched ST.order #{order_number}", flush=True)

    # ---- Find Slack thread ----
    thread_ts = find_slack_thread(order_number)

    if thread_ts:
        post_slack_thread_reply(thread_ts, comment_text)
        return jsonify({"status": "posted_in_thread"}), 200

    # ---- Fallback: post as new message ----
    slack_client.chat_postMessage(
        channel=SLACK_CHANNEL_ID,
        text=f"💬 *Comment on ST.order #{order_number}*\n{comment_text}\n\n_(Thread not found)_"
    )

    return jsonify({"status": "posted_as_new"}), 200


# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
