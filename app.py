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
active_workers = set()    # orders being processed

print("🚀 App started", flush=True)

# ---------------- HEALTH ----------------
@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

# ---------------- SLACK ----------------
def wait_for_slack_thread(order_number, timeout=90):
    print(f"⏳ Waiting for Slack message ST.order #{order_number}", flush=True)
    start = time.time()

    while time.time() - start < timeout:
        r = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            params={"channel": CHANNEL_ID, "limit": 100},
            timeout=10
        )

        if r.ok:
            for msg in r.json().get("messages", []):
                text = msg.get("text", "")
                m = ORDER_REGEX.search(text)
                if m and m.group(1) == order_number:
                    print("✅ Slack order message FOUND", flush=True)
                    return msg["ts"]

        time.sleep(5)

    print("⏹️ Slack message did not appear in time", flush=True)
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

    order = r.json().get("data", {}).get("order")
    if not order:
        return None

    for edge in order["events"]["edges"]:
        node = edge["node"]
        if node["__typename"] == "CommentEvent":
            return node

    return None

# ---------------- BACKGROUND WORKER ----------------
def process_order(order_number, order_id):
    print(f"🧵 Worker started for order #{order_number}", flush=True)

    try:
        # 1️⃣ Wait for Slack order message
        thread_ts = wait_for_slack_thread(order_number)
        if not thread_ts:
            return

        order_threads[order_number] = thread_ts

        # 2️⃣ Poll for comment
        start = time.time()
        while time.time() - start < 120:
            comment = fetch_latest_comment(order_id)
            if comment:
                ts = comment["createdAt"]
                if last_comment_time.get(order_number) != ts:
                    last_comment_time[order_number] = ts
                    slack_reply(
                        thread_ts,
                        f"💬 *{comment['author']['name']}*\n>{comment['message']}"
                    )
                    print("✅ Comment sent to Slack", flush=True)
                break
            time.sleep(10)

    finally:
        active_workers.discard(order_number)
        print(f"⏹️ Worker stopped for order #{order_number}", flush=True)

# ---------------- WEBHOOK ----------------
@app.route("/webhook/order-updated", methods=["POST"])
def webhook():
    data = request.json or {}
    order_number = str(data.get("order_number") or data.get("name", "")).replace("#", "")
    order_id = data.get("id")

    print(f"\n🔔 Webhook received for order #{order_number}", flush=True)

    if not order_number or not order_id:
        return "Invalid payload", 200

    # 🚫 Prevent duplicate workers
    if order_number in active_workers:
        print("⏭️ Worker already running", flush=True)
        return "Already processing", 200

    active_workers.add(order_number)

    # 🚀 Start background worker
    threading.Thread(
        target=process_order,
        args=(order_number, order_id),
        daemon=True
    ).start()

    # ⚡ RETURN IMMEDIATELY (prevents timeout)
    return "OK", 200

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
