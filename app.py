import os, re, requests, time, threading
from flask import Flask, request

app = Flask(__name__)

# ---------------- ENV ----------------
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

CHANNEL_ID = "C0A068PHZMY"
ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b")

# Memory (resets on restart – OK for free plan)
order_threads = {}        # order_number → thread_ts
last_comment_time = {}    # order_number → createdAt
active_pollers = set()    # orders currently being polled

print("🚀 App started", flush=True)

# ---------------- HEALTH ----------------
@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

# ---------------- SLACK ----------------
def find_thread_ts(order_number):
    print(f"🔍 Searching Slack for ST.order #{order_number}", flush=True)

    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": CHANNEL_ID, "limit": 500},
        timeout=10
    )

    if not r.ok:
        print("❌ Slack API error", r.text, flush=True)
        return None

    for msg in r.json().get("messages", []):
        m = ORDER_REGEX.search(msg.get("text", ""))
        if m and m.group(1) == order_number:
            print("✅ Slack order thread found", flush=True)
            return msg["ts"]

    print("❌ Slack order thread NOT found", flush=True)
    return None


def slack_reply(thread_ts, text):
    print("📤 Sending Slack reply", flush=True)

    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "channel": CHANNEL_ID,
            "thread_ts": thread_ts,
            "text": text
        },
        timeout=10
    )

# ---------------- SHOPIFY ----------------
def fetch_latest_comment(order_id):
    query = """
    query ($id: ID!) {
      order(id: $id) {
        events(first: 5, reverse: true) {
          edges {
            node {
              __typename
              ... on CommentEvent {
                message
                createdAt
                author { name }
              }
            }
          }
        }
      }
    }
    """

    r = requests.post(
        f"https://{SHOP}/admin/api/2024-01/graphql.json",
        headers={
            "X-Shopify-Access-Token": SHOPIFY_TOKEN,
            "Content-Type": "application/json"
        },
        json={
            "query": query,
            "variables": {"id": f"gid://shopify/Order/{order_id}"}
        },
        timeout=10
    )

    data = r.json().get("data", {}).get("order")
    if not data:
        return None

    for edge in data["events"]["edges"]:
        node = edge["node"]
        if node["__typename"] == "CommentEvent":
            return node

    return None

# ---------------- POLLER ----------------
def poll_comments(order_number, order_id):
    print(f"👀 Polling comments for order #{order_number}", flush=True)

    start = time.time()

    try:
        while time.time() - start < 120:  # ⏱️ max 2 minutes
            comment = fetch_latest_comment(order_id)
            if comment:
                ts = comment["createdAt"]
                if last_comment_time.get(order_number) != ts:
                    last_comment_time[order_number] = ts
                    thread_ts = order_threads.get(order_number)
                    if thread_ts:
                        slack_reply(
                            thread_ts,
                            f"💬 *{comment['author']['name']}*\n>{comment['message']}"
                        )
                        print("✅ Comment sent to Slack", flush=True)
                break
            time.sleep(10)
    finally:
        active_pollers.discard(order_number)
        print(f"⏹️ Poller stopped for order #{order_number}", flush=True)

# ---------------- WEBHOOK ----------------
@app.route("/webhook/order-updated", methods=["POST"])
def webhook():
    data = request.json or {}
    order_number = str(data.get("order_number") or data.get("name", "")).replace("#", "")
    order_id = data.get("id")

    print(f"\n🔔 Webhook received for order #{order_number}", flush=True)

    if not order_number or not order_id:
        return "Invalid payload", 200

    if order_number not in order_threads:
        ts = find_thread_ts(order_number)
        if not ts:
            return "Slack thread not found", 200
        order_threads[order_number] = ts

    # 🚫 Prevent duplicate pollers
    if order_number in active_pollers:
        print("⏭️ Poller already running", flush=True)
        return "Poller already running", 200

    active_pollers.add(order_number)

    threading.Thread(
        target=poll_comments,
        args=(order_number, order_id),
        daemon=True
    ).start()

    return "OK", 200

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
