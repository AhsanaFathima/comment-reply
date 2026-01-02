import os, re, requests, time, threading
from flask import Flask, request

app = Flask(__name__)

# ---------------- ENV ----------------
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SHOP = os.getenv("SHOPIFY_SHOP")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

CHANNEL_ID = "C0A068PHZMY"
ORDER_REGEX = re.compile(r"\bST\.order\s+#(\d+)\b")

# Memory
order_threads = {}        # order_number → thread_ts
last_comment_time = {}    # order_number → createdAt

# ---------------- SLACK ----------------
def find_thread_ts(order_number):
    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": CHANNEL_ID, "limit": 500}
    )
    for msg in r.json().get("messages", []):
        m = ORDER_REGEX.search(msg.get("text", ""))
        if m and m.group(1) == order_number:
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
        }
    )
    data = r.json().get("data", {}).get("order")
    if not data:
        return None
    for edge in data["events"]["edges"]:
        if edge["node"]["__typename"] == "CommentEvent":
            return edge["node"]
    return None

# ---------------- POLLER ----------------
def poll_comments(order_number, order_id):
    while True:
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
                break
        time.sleep(15)

# ---------------- WEBHOOK ----------------
@app.route("/webhook/order-updated", methods=["POST"])
def webhook():
    data = request.json
    order_number = str(data.get("order_number"))
    order_id = data.get("id")

    if order_number not in order_threads:
        ts = find_thread_ts(order_number)
        if not ts:
            return "Thread not found", 200
        order_threads[order_number] = ts

    threading.Thread(
        target=poll_comments,
        args=(order_number, order_id),
        daemon=True
    ).start()

    return "OK", 200

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
