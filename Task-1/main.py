#!/usr/bin/env python3
"""
Task-1 final evaluator (Ollama / llama3 local).
- Sample N reviews (default 20)
- 3 prompt styles: zero_shot, few_shot, hidden_cot
- repeats per review to measure consistency
- Outputs clean CSV and prints comparison table + short discussion
"""

import os
import json
import time
import argparse
import subprocess
import re
from collections import Counter, defaultdict

import pandas as pd
from tqdm import tqdm

# --------------------
MODEL = "llama3"  # change if needed
# --------------------

# ANSI + spinner cleanup (robust)
ANSI = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
SPINNERS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

def clean_ansi(text: str) -> str:
    if not isinstance(text, str):
        return ""
    t = ANSI.sub("", text)
    for s in SPINNERS:
        t = t.replace(s, "")
    # remove common leftover codes
    t = t.replace("[?25l", "").replace("[?25h", "")
    return t.strip()

def call_ollama(prompt: str, timeout: int = 60) -> str:
    """Call local Ollama model. Returns cleaned stdout or an <ERROR: ...> string."""
    env = os.environ.copy()
    env["OLLAMA_NO_TTY"] = "1"

    try:
        p = subprocess.Popen(
            ["ollama", "run", MODEL],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env
        )
        out, err = p.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        return "<ERROR: TIMEOUT>"
    except Exception as e:
        return f"<ERROR: {e}>"

    clean_out = clean_ansi(out)
    clean_err = clean_ansi(err)

    # treat stderr as error only if it contains real keywords
    if clean_err and ("error" in clean_err.lower() or "panic" in clean_err.lower()):
        return f"<ERROR: {clean_err}>"

    return clean_out

# --------------------
# Prompt templates (all braces that are literal are doubled)
# Only {review} is a format placeholder.
# --------------------
PROMPTS = {
    "zero_shot": (
        "You are a rating classifier. Read the review and assign a rating 1–5.\n"
        "Return ONLY JSON (no explanation):\n"
        "{{\n"
        '  "predicted_stars": <number>,\n'
        '  "explanation": "<brief reason>"\n'
        "}}\n\n"
        "Review:\n{review}"
    ),

    "few_shot": (
        "You classify reviews into 1–5 stars. Follow the JSON exactly.\n\n"
        "Examples:\n"
        "Review: 'Amazing food, friendly staff.' -> {{\"predicted_stars\": 5, \"explanation\": \"strong positive\"}}\n"
        "Review: 'Cold food and slow service.' -> {{\"predicted_stars\": 2, \"explanation\": \"negative: service/food\"}}\n\n"
        "Now classify this review and return ONLY JSON:\n{review}\n\n"
        "{{\"predicted_stars\": <number>, \"explanation\": \"<short>\"}}"
    ),

    "hidden_cot": (
        "You are a professional classifier. Think step-by-step internally (DO NOT reveal thoughts).\n"
        "Finally output ONLY JSON in this format:\n"
        "{{\n"
        '  "predicted_stars": <number>,\n'
        '  "explanation": "<brief justification>"\n'
        "}}\n\n"
        "Review:\n{review}"
    ),
}

# --------------------
# JSON parsing helper that tolerates:
# - single object { ... }
# - list [ {...}, ... ] (we take first or the only)
# - code fences ```json ... ```
# - text before/after JSON
# --------------------
def try_parse_json_flexible(text: str):
    if not isinstance(text, str):
        return None, "not a string"

    s = text.strip()
    # remove code fences
    s = s.replace("```json", "").replace("```", "").strip()

    # direct parse
    try:
        parsed = json.loads(s)
        # If list, pick first dict
        if isinstance(parsed, list):
            if len(parsed) == 0:
                return None, "empty list"
            item = parsed[0]
            if isinstance(item, dict):
                return item, ""
            return None, "first item not dict"
        if isinstance(parsed, dict):
            return parsed, ""
    except Exception:
        pass

    # try extract { ... } section (largest)
    try:
        first = s.index("{")
        last = s.rindex("}") + 1
        snippet = s[first:last]
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed, ""
        else:
            return None, "extracted JSON not dict"
    except Exception as e:
        return None, f"parse failed: {e}"

# normalize predicted stars into valid 1-5 int or -1
def normalize_stars(v):
    try:
        if v is None:
            return -1
        if isinstance(v, (int, float)):
            val = int(round(v))
        elif isinstance(v, str):
            # handle "5", "5.0"
            if v.strip().isdigit():
                val = int(v.strip())
            else:
                val = int(float(v.strip()))
        else:
            val = int(v)
    except Exception:
        return -1
    if val < 1:
        val = 1
    if val > 5:
        val = 5
    return val

# --------------------
# Evaluation pipeline
# --------------------
def evaluate(df, n=200, repeats=3, out_dir="outputs"):
    df = df.head(n).reset_index(drop=True)

    results = []  # rows for final CSV

    # per-style aggregates
    summary = []

    for style, template in PROMPTS.items():
        valid_counts = []
        accuracies = []
        consistencies = []

        for idx, row in tqdm(df.iterrows(), total=len(df), desc=f"style={style}"):
            review_text = str(row["text"]).replace('"', "'")
            true_label = int(row["stars"])

            repeat_preds = []
            repeat_expls = []
            valid_count = 0

            for r in range(repeats):
                prompt = template.format(review=review_text)
                raw = call_ollama(prompt)

                # parse
                parsed, err = try_parse_json_flexible(raw)
                if parsed is None:
                    repeat_preds.append(None)
                    repeat_expls.append(None)
                else:
                    # prefer keys predicted_stars or label
                    if "predicted_stars" in parsed:
                        s = parsed.get("predicted_stars")
                    elif "label" in parsed:
                        s = parsed.get("label")
                    else:
                        s = parsed.get("stars", None)

                    explanation = parsed.get("explanation") or parsed.get("reason") or parsed.get("justification") or ""
                    norm = normalize_stars(s)
                    repeat_preds.append(norm if norm >= 1 else None)
                    repeat_expls.append(explanation if explanation else None)
                    valid_count += 1

            # JSON validity rate
            json_validity_rate = valid_count / repeats

            # consistency: fraction of most common non-None prediction among valid repeats
            preds_valid = [p for p in repeat_preds if p is not None]
            if len(preds_valid) == 0:
                consistency = 0.0
                final_pred = -1
                final_expl = ""
            else:
                counter = Counter(preds_valid)
                final_pred = counter.most_common(1)[0][0]
                consistency = counter.most_common(1)[0][1] / len(preds_valid)
                # pick the explanation corresponding to the most common pred (first match)
                final_expl = ""
                for p, e in zip(repeat_preds, repeat_expls):
                    if p == final_pred and e:
                        final_expl = e
                        break

            # accuracy (for this single sample)
            accuracy = 1.0 if final_pred == true_label and final_pred != -1 else 0.0

            valid_counts.append(json_validity_rate)
            accuracies.append(accuracy)
            consistencies.append(consistency)

            # store a clean row — minimal columns required by you
            results.append({
                "style": style,
                "review": row["text"],
                "true_label": true_label,
                "predicted_stars": int(final_pred) if final_pred != -1 else -1,
                "explanation": final_expl,
                "json_validity_rate": round(json_validity_rate, 3),
                "consistency": round(consistency, 3)
            })

        # aggregate per-style
        avg_accuracy = sum(accuracies) / len(accuracies) if len(accuracies) > 0 else 0.0
        avg_json_valid = sum(valid_counts) / len(valid_counts) if len(valid_counts) > 0 else 0.0
        avg_consistency = sum(consistencies) / len(consistencies) if len(consistencies) > 0 else 0.0

        summary.append({
            "prompt_style": style,
            "accuracy": round(avg_accuracy, 3),
            "json_validity_rate": round(avg_json_valid, 3),
            "avg_consistency": round(avg_consistency, 3),
            "num_examples": len(df)
        })

    # Save results CSV and summary
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time())
    results_df = pd.DataFrame(results)
    results_csv = os.path.join(out_dir, f"results_{ts}.csv")
    summary_csv = os.path.join(out_dir, f"summary_{ts}.json")

    results_df.to_csv(results_csv, index=False, encoding="utf-8")
    with open(summary_csv, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Print comparison table
    print("\n=== Comparison Table ===")
    print(pd.DataFrame(summary).to_string(index=False))

    # Short discussion
    print("\n=== Short discussion ===")
    for s in summary:
        print(f"- {s['prompt_style']}: accuracy={s['accuracy']}, json_validity={s['json_validity_rate']}, consistency={s['avg_consistency']}")

    print(f"\nSaved results -> {results_csv}")
    print(f"Saved summary  -> {summary_csv}")

    return results_df, summary

# --------------------
# CLI
# --------------------
def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, default="yelp_reviews.csv", help="path to CSV dataset")
    parser.add_argument("--n", type=int, default=20, help="number of rows to sample")
    parser.add_argument("--repeats", type=int, default=3, help="repeats per review for consistency")
    parser.add_argument("--out", type=str, default="outputs", help="output folder")
    args = parser.parse_args()

    df = pd.read_csv(args.data)
    # quick column detection
    if "text" not in df.columns or ("stars" not in df.columns and "rating" not in df.columns and "star" not in df.columns):
        raise ValueError("Dataset must contain 'text' and 'stars' columns (or rename accordingly).")
    # ensure we have 'stars' column
    if "stars" not in df.columns:
        possible = [c for c in df.columns if "star" in c.lower() or "rating" in c.lower()]
        df = df.rename(columns={possible[0]: "stars"})

    evaluate(df, n=args.n, repeats=args.repeats, out_dir=args.out)

if __name__ == "__main__":
    main_cli()
