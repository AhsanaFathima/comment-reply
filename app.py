import os, re, requests, hashlib
from flask import Flask, request

app = Flask(__name__)

# ---------------- ENV ----------------
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

# ✅ UPDATED CHANNEL ID
CHANNEL_ID = "C0A068PHZMY"

# STRICT MATCH: ONLY "ST.order #1234"
ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b")

# In-memory stores (OK for now)
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
        params={"channel": CHANNEL_ID, "limit": 100}
    )

    if not r.ok:
        print("❌ Slack history API failed:", r.text, flush=True)
        return None

    for msg in r.json().get("messages", []):
        text = msg.get("text", "")
        match = ORDER_REGEX.search(text)
        if match and match.group(1) == order_number:
            print("✅ Found Slack order message:", text, flush=True)
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
        events(first: 5, reverse: true) {
          edges {
            node {
              __typename
              ... on OrderCommentEvent {
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
        print("❌ Invalid JSON from Shopify:", r.text, flush=True)
        return None

    # 🔴 GraphQL error handling
    if "errors" in payload:
        print("❌ Shopify GraphQL errors:", payload["errors"], flush=True)
        return None

    data = payload.get("data")
    if not data:
        print("⏭️ No data returned from Shopify", flush=True)
        return None

    order = data.get("order")
    if not order:
        print("⏭️ Order not found in GraphQL response", flush=True)
        return None

    events = order.get("events", {}).get("edges", [])
    if not events:
        print("⏭️ No events found", flush=True)
        return None

    for edge in events:
        node = edge.get("node", {})
        if node.get("__typename") == "OrderCommentEvent":
            print("💬 Found timeline comment:", node["message"], flush=True)
            return node

    print("⏭️ No timeline comments in events", flush=True)
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
    print("🆔 Order ID:", order_id, flush=True)

    # Find Slack thread
    thread_ts = order_threads.get(order_number) or find_thread_ts(order_number)
    if not thread_ts:
        print("❌ Slack thread not found", flush=True)
        return "No Slack thread", 200

    order_threads[order_number] = thread_ts

    # Fetch comment
    comment = fetch_latest_comment(order_id)
    if not comment:
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
