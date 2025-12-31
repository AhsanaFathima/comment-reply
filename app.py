import os, re, requests, hashlib, time
from flask import Flask, request

app = Flask(__name__)

# ---------------- ENV ----------------
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

# Slack channel
CHANNEL_ID = "C0A068PHZMY"

# ✅ FIXED REGEX (allow text after order number)
ORDER_REGEX = re.compile(r"ST\.order\s+#(\d+)")

# In-memory stores
order_threads = {}
processed_comments = set()

print("🚀 App started", flush=True)
print("🏪 Shopify shop:", SHOP, flush=True)
print("📢 Slack channel:", CHANNEL_ID, flush=True)

# --------------------------------------------------
# 🔍 Find Slack thread timestamp
# --------------------------------------------------
def find_thread_ts(order_number):
    print(f"🔍 Searching Slack thread for order #{order_number}", flush=True)

    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": CHANNEL_ID, "limit": 500}
    )

    if not r.ok:
        print("❌ Slack history API failed:", r.text, flush=True)
        return None

    for msg in r.json().get("messages", []):
        text = msg.get("text", "")
        match = ORDER_REGEX.search(text)
        if match and match.group(1) == order_number:
            print("✅ Found Slack order message", flush=True)
            return msg["ts"]

    print("❌ No Slack thread found", flush=True)
    return None

# --------------------------------------------------
# 💬 Send Slack thread reply
# --------------------------------------------------
def slack_reply(thread_ts, text):
    print("📤 Sending Slack thread reply", flush=True)

    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "channel": CHANNEL_ID,
            "thread_ts": thread_ts,
            "text": text
        }
    )

    if r.ok and r.json().get("ok"):
        print("✅ Slack reply sent", flush=True)
    else:
        print("❌ Slack reply failed:", r.text, flush=True)

# --------------------------------------------------
# 🧠 Shopify GraphQL — fetch timeline comments
# --------------------------------------------------
def fetch_latest_comment(order_id):
    print("🧠 Fetching Shopify timeline comments", flush=True)

    url = f"https://{SHOP}/admin/api/2024-01/graphql.json"

    query = """
    query ($id: ID!) {
      order(id: $id) {
        events(first: 10, reverse: true) {
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
        url,
        headers={
            "X-Shopify-Access-Token": SHOPIFY_TOKEN,
            "Content-Type": "application/json"
        },
        json={
            "query": query,
            "variables": {"id": f"gid://shopify/Order/{order_id}"}
        }
    )

    try:
        payload = r.json()
    except Exception:
        print("❌ Invalid JSON from Shopify", flush=True)
        return None

    if "errors" in payload:
        print("❌ Shopify GraphQL errors:", payload["errors"], flush=True)
        return None

    order = payload.get("data", {}).get("order")
    if not order:
        return None

    events = order.get("events", {}).get("edges", [])
    for edge in events:
        node = edge.get("node", {})
        if node.get("__typename") == "CommentEvent":
            print("💬 Found timeline comment:", node["message"], flush=True)
            return node

    print("⏭️ No timeline comments yet", flush=True)
    return None

# --------------------------------------------------
# 🔔 Shopify Webhook
# --------------------------------------------------
@app.route("/webhook/order-updated", methods=["POST"])
def order_updated():
    print("\n🔔 Shopify ORDER UPDATED webhook received", flush=True)

    data = request.json
    order_number = str(data.get("order_number"))
    order_id = data.get("id")

    print("🧾 Order number:", order_number, flush=True)

    # Find Slack thread
    thread_ts = order_threads.get(order_number) or find_thread_ts(order_number)
    if not thread_ts:
        print("❌ Slack thread not found", flush=True)
        return "No Slack thread", 200

    order_threads[order_number] = thread_ts

    # 🔁 FIX: Retry once after delay
    comment = fetch_latest_comment(order_id)

    if not comment:
        print("⏳ No comment yet — retrying in 5 seconds", flush=True)
        time.sleep(5)
        comment = fetch_latest_comment(order_id)

    if not comment:
        print("❌ Still no comment after retry", flush=True)
        return "No comment", 200

    # Dedup
    dedup_key = hashlib.md5(
        f"{comment['message']}{comment['createdAt']}".encode()
    ).hexdigest()

    if dedup_key in processed_comments:
        print("🔁 Duplicate comment ignored", flush=True)
        return "Duplicate", 200

    processed_comments.add(dedup_key)

    # Reply in Slack thread
    slack_reply(
        thread_ts,
        f"💬 *{comment['author']['name']}*\n>{comment['message']}"
    )

    return "OK", 200

# --------------------------------------------------
# 🚀 Run app (Render-compatible)
# --------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Starting server on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
