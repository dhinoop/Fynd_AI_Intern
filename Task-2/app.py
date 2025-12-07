import os
import json
import time
import queue
import threading
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from groq import Groq
from dotenv import load_dotenv

# ---------------------------------------------------------
# LOAD ENVIRONMENT (FORCE PATH)
# ---------------------------------------------------------
load_dotenv(dotenv_path=r"C:\Users\DHINOOP\OneDrive\Documents\Fynd_AI_Intern\Task-2\.env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# ---------------------------------------------------------
# GROQ CLIENT
# ---------------------------------------------------------
if not GROQ_API_KEY:
    print("WARNING: No GROQ_API_KEY found. AI disabled.")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# MODEL AVAILABLE IN YOUR ACCOUNT
MODEL = "llama-3.1-8b-instant"

# ---------------------------------------------------------
# DATA STORAGE
# ---------------------------------------------------------
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "reviews.json")
os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

# Create file if missing
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

# ---------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------
app = Flask(__name__)
CORS(app)

listeners = []
listeners_lock = threading.Lock()

# ---------------------------------------------------------
# AI FUNCTIONS (PATCHED)
# ---------------------------------------------------------

def generate_ai_reply(rating, review_text):
    """Return a friendly AI response."""
    if not client:
        return f"Thank you for your {rating}-star review!"

    prompt = (
        f"A user gave a rating of {rating}/5.\n"
        f"Review: \"{review_text}\"\n"
        "Write a short, friendly reply thanking them."
    )

    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120
        )
        # FIXED: correct content access
        return completion.choices[0].message.content.strip()

    except Exception as e:
        print("Groq Error (reply):", e)
        return f"Thank you for your {rating}-star review!"


def generate_summary_and_actions(rating, review_text):
    """Return JSON summary + actions."""
    if not client:
        return review_text[:120], ["Manual review required"]

    prompt = (
        f"Review rating: {rating}/5\n"
        f"Review: {review_text}\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        "  \"summary\": \"one sentence summary\",\n"
        "  \"actions\": [\"action1\", \"action2\", \"action3\"]\n"
        "}"
    )

    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200
        )

        # FIXED: correct format
        text = completion.choices[0].message.content.strip()

        data = json.loads(text)

        return data.get("summary", ""), data.get("actions", [])

    except Exception as e:
        print("Groq JSON Error:", e)
        return review_text[:120], ["Manual review required"]

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/")
def user_dashboard():
    return render_template("user.html")

@app.route("/admin")
def admin_dashboard():
    pwd = request.args.get("pwd")
    if pwd != ADMIN_PASSWORD:
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

        # Push live update
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

@app.route("/api/stats")
def stats():
    arr = read_reviews()
    total = len(arr)
    avg_rating = round(sum(r["rating"] for r in arr) / total, 2) if total else 0
    return jsonify({"total": total, "avg_rating": avg_rating})

# ---------------------------------------------------------
# SSE STREAM
# ---------------------------------------------------------

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

# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
