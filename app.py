import os
import re
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# In-memory store (already exists in your app)
order_threads = {}

def post_slack_reply(channel, thread_ts, message):
    print("📤 Sending reply to Slack...", flush=True)
    print(f"   ➤ Channel: {channel}", flush=True)
    print(f"   ➤ Thread TS: {thread_ts}", flush=True)
    print(f"   ➤ Message: {message}", flush=True)

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": message
    }

    r = requests.post(url, headers=headers, json=payload)
    print(f"✅ Slack API response: {r.status_code} | {r.text}", flush=True)


@app.route("/webhook/order-updated", methods=["POST"])
def order_updated():
    print("🔔 Shopify ORDER UPDATED webhook received", flush=True)

    data = request.json
    if not data:
        print("❌ No JSON payload received", flush=True)
        return "Invalid payload", 400

    print("📦 Full payload keys:", list(data.keys()), flush=True)

    order_number = data.get("order_number")
    note = data.get("note")

    print(f"🧾 Order number: {order_number}", flush=True)
    print(f"📝 Order note (comment): {note}", flush=True)

    # 🚫 No comment → ignore
    if not note:
        print("⏭️ No comment found — ignoring webhook", flush=True)
        return "No comment", 200

    order_number = str(order_number)

    # 🚫 Order not tracked in Slack
    thread_info = order_threads.get(order_number)
    if not thread_info:
        print(f"❌ Order #{order_number} not found in order_threads", flush=True)
        return "Order not found in Slack threads", 200

    channel = thread_info.get("channel")
    thread_ts = thread_info.get("thread_ts")

    print("🧵 Slack thread found", flush=True)
    print(f"   ➤ Channel: {channel}", flush=True)
    print(f"   ➤ Thread TS: {thread_ts}", flush=True)

    # ✅ Post as thread reply
    post_slack_reply(
        channel,
        thread_ts,
        f"💬 *Shopify Comment:*\n>{note}"
    )

    print("🎉 Comment successfully posted to Slack thread", flush=True)
    return "Comment sent to Slack", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Flask app on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
