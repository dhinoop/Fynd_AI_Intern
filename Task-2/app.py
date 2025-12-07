import os
import json
import time
import queue
import threading
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from dotenv import load_dotenv
import ollama

load_dotenv()

print("RUNNING FROM:", os.getcwd())

# --------------------------------------------------------
# DATA STORAGE (JSON FILE)
# --------------------------------------------------------

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "reviews.json")
os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

if not os.path.exists(DATA_PATH):
    with open(DATA_PATH, "w") as f:
        json.dump([], f)


def read_reviews():
    with open(DATA_PATH, "r") as f:
        return json.load(f)


def write_review(obj):
    arr = read_reviews()
    arr.append(obj)
    with open(DATA_PATH, "w") as f:
        json.dump(arr, f, indent=2)


# --------------------------------------------------------
# FLASK SETUP
# --------------------------------------------------------

app = Flask(__name__)
CORS(app)

listeners = []
listeners_lock = threading.Lock()


# --------------------------------------------------------
# OLLAMA AI FUNCTIONS (NO API KEY REQUIRED)
# --------------------------------------------------------

def generate_ai_reply(rating, review_text):
    prompt = (
        f"A user gave a rating of {rating}/5.\n"
        f"Review: {review_text}\n"
        "Write a friendly 2–3 sentence reply thanking them."
    )

    try:
        response = ollama.generate(
            model="llama3",
            prompt=prompt
        )
        return response["response"].strip()
    except Exception as e:
        print("Ollama error (reply):", e)
        return f"Thank you for your {rating}-star review!"


def generate_summary_and_actions(rating, review_text):
    prompt = (
        f"Review rating: {rating}/5\n"
        f"Review: {review_text}\n\n"
        "Write JSON ONLY with keys:\n"
        "{\"summary\": \"...\", \"actions\": [\"...\", \"...\"]}"
    )

    try:
        response = ollama.generate(
            model="llama3",
            prompt=prompt
        )
        text = response["response"].strip()

        try:
            data = json.loads(text)
            return data.get("summary", ""), data.get("actions", [])
        except:
            return review_text[:120], ["Check manually", "Improve service"]
    except Exception as e:
        print("Ollama JSON error:", e)
        return review_text[:120], ["Check manually", "Improve service"]


# --------------------------------------------------------
# ROUTES
# --------------------------------------------------------

@app.route("/")
def user_dashboard():
    return render_template("user.html")


@app.route("/admin")
def admin_dashboard():
    pwd = request.args.get("pwd")
    expected = os.getenv("ADMIN_PASSWORD", "admin123")

    if pwd != expected:
        return render_template("admin.html", authorized=False)

    return render_template("admin.html", authorized=True, admin_pwd=pwd)


@app.route("/api/submit", methods=["POST"])
def submit_review():
    try:
        data = request.json
        rating = int(data.get("rating"))
        review = data.get("review").strip()

        ai_reply = generate_ai_reply(rating, review)
        summary, actions = generate_summary_and_actions(rating, review)

        review_obj = {
            "id": f"r_{int(time.time())}",
            "rating": rating,
            "review": review,
            "ai_reply": ai_reply,
            "summary": summary,
            "actions": actions,
            "ts": int(time.time())
        }

        write_review(review_obj)

        # Notify all admin SSE listeners
        with listeners_lock:
            for q in listeners:
                try:
                    q.put(review_obj, block=False)
                except:
                    pass

        return jsonify({"ok": True, "ai_reply": ai_reply})

    except Exception as e:
        print("🔥 BACKEND ERROR:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/reviews")
def get_reviews():
    return jsonify(read_reviews())


from datetime import datetime, timezone, timedelta

@app.route("/api/stats")
def stats():
    arr = read_reviews()
    total = len(arr)
    avg_rating = round(sum(r["rating"] for r in arr) / total, 2) if total else 0

    # Rating distribution 1..5
    rating_counts = {str(i): 0 for i in range(1, 6)}
    for r in arr:
        rating_counts[str(r.get("rating", 0))] = rating_counts.get(str(r.get("rating", 0)), 0) + 1

    # Recent trend: last 7 days (labels and counts)
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]  # 7 days, oldest->newest
    day_labels = [d.strftime("%Y-%m-%d") for d in days]
    day_counts = {label: 0 for label in day_labels}

    for r in arr:
        ts = r.get("ts")
        if not ts:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().strftime("%Y-%m-%d")
        if d in day_counts:
            day_counts[d] += 1

    day_counts_list = [day_counts[label] for label in day_labels]

    return jsonify({
        "total": total,
        "avg_rating": avg_rating,
        "rating_counts": [rating_counts[str(i)] for i in range(1, 6)],
        "trend_labels": day_labels,
        "trend_counts": day_counts_list
    })



# --------------------------------------------------------
# SSE STREAM (LIVE ADMIN UPDATES)
# --------------------------------------------------------

@app.route("/stream")
def stream():
    def event_stream(q):
        while True:
            item = q.get()
            yield f"data: {json.dumps(item)}\n\n"

    q = queue.Queue()

    with listeners_lock:
        listeners.append(q)

    return Response(event_stream(q), mimetype="text/event-stream")


# --------------------------------------------------------
# RUN SERVER
# --------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=True
    )
