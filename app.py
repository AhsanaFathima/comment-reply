import os, re, requests, hashlib
from flask import Flask, request

app = Flask(__name__)

SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
CHANNEL_ID = "C0A02M2VCTB"

ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b")

# Memory (replace with DB later)
order_threads = {}
processed_comments = set()

# ---------------- SLACK ----------------
def find_thread_ts(order_number):
    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": CHANNEL_ID, "limit": 100}
    )
    for msg in r.json().get("messages", []):
        if ORDER_REGEX.search(msg.get("text", "")):
            if ORDER_REGEX.search(msg["text"]).group(1) == order_number:
                return msg["ts"]
    return None

def slack_reply(thread_ts, text):
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
        }
    )

# ---------------- SHOPIFY GRAPHQL ----------------
def fetch_latest_comment(order_id):
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

    for edge in r.json()["data"]["order"]["events"]["edges"]:
        node = edge["node"]
        if node["__typename"] == "OrderCommentEvent":
            return node
    return None

# ---------------- WEBHOOK ----------------
@app.route("/webhook/order-updated", methods=["POST"])
def order_updated():
    data = request.json

    order_number = str(data.get("order_number"))
    order_id = data.get("id")

    thread_ts = order_threads.get(order_number) or find_thread_ts(order_number)
    if not thread_ts:
        return "No Slack thread", 200

    order_threads[order_number] = thread_ts

    comment = fetch_latest_comment(order_id)
    if not comment:
        return "No comment", 200

    dedup_key = hashlib.md5(
        f"{comment['message']}{comment['createdAt']}".encode()
    ).hexdigest()

    if dedup_key in processed_comments:
        return "Duplicate", 200

    processed_comments.add(dedup_key)

    slack_reply(
        thread_ts,
        f"💬 *{comment['author']['name']}*\n>{comment['message']}"
    )

    return "OK", 200

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
