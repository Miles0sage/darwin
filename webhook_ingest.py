#!/usr/bin/env python3
"""
Darwin Webhook Ingest — production telemetry → fingerprint → heal.

POST /darwin/failure with a Sentry/Datadog-style stack trace payload.
Darwin fingerprints it, looks up the blackboard, applies a cached LibCST
transformer if available, or diagnoses via LLM on a miss. Returns the
healed source + metadata.

Separates Darwin from inner-loop dev tools (Devin / Aider / SWE-agent):
  those fire during human edit loops; Darwin fires from production
  telemetry webhooks — the outer loop.

Usage:
  python3 webhook_ingest.py               # listens on :7777
  curl -X POST localhost:7777/darwin/failure -d '{"stderr":"...","source_code":"..."}'
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from flask import Flask, jsonify, request  # noqa: E402

import blackboard  # noqa: E402
import patch  # noqa: E402
import signature  # noqa: E402
from darwin_harness import diagnose_and_fix, validate_fix  # noqa: E402

app = Flask(__name__)

# ─── counters (process-local; persisted in blackboard JSON for durability) ──
_counters = {
    "requests": 0,
    "cache_hits": 0,
    "llm_diagnoses": 0,
    "gate_rejections": 0,
    "heals": 0,
    "startup_ts": datetime.now(timezone.utc).isoformat(),
}


def _err(msg: str, code: int = 400):
    return jsonify({"status": "error", "error": msg}), code


@app.route("/darwin/failure", methods=["POST"])
def failure():
    """Ingest a production failure. Fingerprint → heal or diagnose."""
    _counters["requests"] += 1
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as e:
        return _err(f"invalid JSON: {e}")
    if not isinstance(payload, dict):
        return _err("payload must be a JSON object")

    stderr = payload.get("stderr") or payload.get("stack_trace") or payload.get("message")
    if not stderr:
        return _err("payload must include 'stderr' / 'stack_trace' / 'message'")
    source_code = payload.get("source_code")
    originating_agent = payload.get("originating_agent") or payload.get("agent") or "webhook"

    fp, normalized = signature.fingerprint(stderr)
    err_class = signature.error_class(stderr)
    resp: dict = {
        "fingerprint": fp,
        "error_class": err_class,
        "normalized_signature": normalized,
    }

    # Cache lookup.
    prior = blackboard.lookup(stderr)
    if prior is not None:
        _counters["cache_hits"] += 1
        resp["cache_hit"] = True
        resp["patch_origin"] = prior.get("originating_agent")
        resp["llm_provider"] = prior.get("llm_provider", "unknown")
        transformer_src = prior.get("transformer_src")
        resp["has_transformer"] = bool(transformer_src)
        if source_code and transformer_src:
            ok, new_src, err = patch.try_apply(source_code, patch.PatchRecipe(transformer_src=transformer_src))
            if ok:
                ok_gate, reasons = validate_fix(source_code, new_src, stderr)
                if not ok_gate:
                    _counters["gate_rejections"] += 1
                    blackboard.log_rejected(stderr, reasons, new_src)
                    resp["status"] = "gate_rejected"
                    resp["gate_reasons"] = reasons
                    return jsonify(resp), 422
                _counters["heals"] += 1
                resp["status"] = "healed"
                resp["new_source"] = new_src
                resp["patch_applied"] = True
                return jsonify(resp)
            resp["status"] = "cache_hit_pattern_miss"
            resp["pattern_miss_reason"] = err
            # fall through → LLM adapter below (B-path)
        elif source_code and not transformer_src:
            # Legacy entry: serve the full fix_code as a fallback — noted.
            resp["status"] = "cache_hit_legacy_fix"
            resp["new_source"] = prior.get("fix_code")
            return jsonify(resp)
        else:
            resp["status"] = "cache_hit_no_source_supplied"
            return jsonify(resp)

    resp["cache_hit"] = False

    # B-path: diagnose via LLM (or heuristic fallback), gate, cache.
    if not source_code:
        resp["status"] = "cache_miss_no_source"
        return jsonify(resp), 404

    _counters["llm_diagnoses"] += 1
    fix_code = diagnose_and_fix(source_code, stderr)
    if fix_code is None:
        resp["status"] = "diagnose_failed"
        blackboard.log_failed_attempt(stderr, "webhook diagnose returned None")
        return jsonify(resp), 500

    ok_gate, reasons = validate_fix(source_code, fix_code, stderr)
    if not ok_gate:
        _counters["gate_rejections"] += 1
        blackboard.log_rejected(stderr, reasons, fix_code)
        resp["status"] = "gate_rejected"
        resp["gate_reasons"] = reasons
        return jsonify(resp), 422

    # Cache (full source as legacy fallback; transformer_src would be
    # produced by a future LLM-that-returns-a-CSTTransformer path).
    entry = blackboard.write_fix(
        stderr,
        root_cause=f"webhook diagnose: {err_class}",
        fix_code=fix_code,
        originating_agent=originating_agent,
    )
    # Seed a reference transformer for known classes (fastest demo path).
    seed = patch.reference_recipe_for(err_class)
    if seed is not None:
        entry_path = blackboard.FIXES_DIR / f"fix-{entry['timestamp']}.json"
        d = json.loads(entry_path.read_text())
        d["transformer_src"] = seed.transformer_src
        entry_path.write_text(json.dumps(d, indent=2))

    _counters["heals"] += 1
    resp["status"] = "diagnosed_and_cached"
    resp["new_source"] = fix_code
    resp["patch_applied"] = True
    return jsonify(resp)


@app.route("/darwin/status", methods=["GET"])
def status():
    """Live counters + blackboard state."""
    return jsonify(
        {
            "counters": _counters,
            "blackboard": {
                "fixes": blackboard.count_fixes(),
                "rejected": blackboard.count_rejected(),
                "fixes_dir": str(blackboard.FIXES_DIR),
            },
            "now": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/darwin/fixes", methods=["GET"])
def fixes():
    """List cached fingerprints (summary only — no full source)."""
    if not blackboard.FIXES_DIR.exists():
        return jsonify({"fixes": []})
    entries = []
    for p in sorted(blackboard.FIXES_DIR.glob("fix-*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        entries.append(
            {
                "fingerprint": d.get("fingerprint"),
                "error_class": d.get("error_class"),
                "timestamp": d.get("timestamp"),
                "originating_agent": d.get("originating_agent"),
                "has_transformer": bool(d.get("transformer_src")),
                "confidence": d.get("confidence", 0.0),
            }
        )
    return jsonify({"fixes": entries, "count": len(entries)})


@app.errorhandler(Exception)
def _any_err(e):
    traceback.print_exc()
    return jsonify({"status": "error", "error": f"{type(e).__name__}: {e}"}), 500


def main() -> None:
    port = int(os.environ.get("DARWIN_WEBHOOK_PORT", "7777"))
    host = os.environ.get("DARWIN_WEBHOOK_HOST", "127.0.0.1")
    print(f"Darwin webhook listening on http://{host}:{port}")
    print("  POST /darwin/failure   ingest a failure payload")
    print("  GET  /darwin/status    live counters")
    print("  GET  /darwin/fixes     list cached fingerprints")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
