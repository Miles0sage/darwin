#!/usr/bin/env python3
"""SentimentTracker Agent — timeout/hang naive version.

BUG: upstream API is slow; naive client has no timeout and no retry.
Simulated via an immediate TimeoutError — a production client using
requests.get() with no `timeout=` kwarg would hang then raise here.
Darwin's job: detect TimeoutError pattern, inject a retry-with-backoff.
"""

import json
import sys
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).parent
_CALL_COUNT = 0


def load_config():
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_posts(api_version: str) -> list:
    # Simulated realistic upstream: 1st call times out, subsequent succeed.
    # Naive client has no retry → crashes on the first call.
    # A correct fix adds retry/backoff so the 2nd call goes through.
    global _CALL_COUNT
    _CALL_COUNT += 1
    if _CALL_COUNT == 1:
        raise TimeoutError("Upstream API did not respond in 10s")
    api_path = BASE_DIR / "api" / api_version / "data.json"
    with open(api_path) as f:
        data = json.load(f)
    return data["posts"]


def analyze_sentiment(text: str) -> str:
    positive = {"love", "great", "awesome", "excellent", "amazing", "good", "best"}
    negative = {"terrible", "awful", "bad", "worst", "hate", "horrible", "poor"}
    words = set(text.lower().split())
    if words & positive:
        return "positive"
    if words & negative:
        return "negative"
    return "neutral"


def run():
    config = load_config()
    api_version = config["api_version"]
    agent_name = config["agent_name"]
    print(f"[{agent_name}] Polling API {api_version}...")
    posts = fetch_posts(api_version)
    results = []
    for post in posts:
        text = post.get("data", {}).get("text") or post.get("text", "")
        sentiment = analyze_sentiment(text)
        results.append({"id": post["id"], "text": text, "sentiment": sentiment})
    for r in results:
        print(f"  #{r['id']} [{r['sentiment']:>8}] {r['text']}")
    print(f"[{agent_name}] Processed {len(results)} posts successfully.")
    return results


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"AGENT FAILURE: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
