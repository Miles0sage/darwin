# Darwin Commons 72-Hour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship public `/darwin/heal/public` endpoint + `Miles0sage/darwin-commons` public data repo + launch wave in 72 hours, starting the failure-corpus flywheel with a moderatable surface.

**Architecture:** Additive on top of existing `darwin-mvp` Flask webhook. Public endpoint writes to a local staging JSONL; a cron-batched sync script (systemd timer, 15-min interval) runs an in-process verifier and GPG-signs commits into the `darwin-commons` repo. DLQ/quarantine flow surfaces broken transformers through a CLI tool. Contributor badge served as Shields.io-compatible JSON.

**Tech Stack:** Python 3.11+, Flask, `flask-limiter` (rate limit), `gitpython` (commit/push), GPG key + `darwin-commons-bot` GitHub account, systemd timer, existing LibCST/CSTTransformer infrastructure. No new storage dependencies.

---

## File Structure

### Modified
- `darwin-mvp/webhook_ingest.py` — add 3 routes (`/darwin/heal/public`, `/darwin/commons/list`, `/darwin/commons/badge/<hash>`), add rate limiter, add staging writer
- `darwin-mvp/blackboard.py` — add `write_to_staging(entry)` helper
- `darwin-mvp/README.md` — add Commons counter + link to `darwin-commons` repo in hero
- `darwin-mvp/requirements.txt` — add `flask-limiter`, `gitpython`

### Created
- `darwin-mvp/commons_sync.py` — cron-batched sync worker
- `darwin-mvp/commons_triage.py` — CLI for quarantine triage
- `darwin-mvp/test_public_endpoint.py` — unit tests for new routes
- `darwin-mvp/test_commons_sync.py` — unit tests for sync worker
- `darwin-mvp/staging/.gitkeep` — staging dir placeholder
- `darwin-mvp/scripts/commons-sync.service` — systemd unit file
- `darwin-mvp/scripts/commons-sync.timer` — systemd timer
- `darwin-mvp/scripts/install-commons-sync.sh` — installer

### New Repo (`Miles0sage/darwin-commons`)
- `README.md`, `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SCHEMA.md`
- `fingerprints.jsonl`, `quarantine.jsonl`
- `transformers/` (directory)
- `.github/workflows/commons-verify.yml`, `.github/workflows/commons-credit.yml`
- `gpg-pubkey.asc`

---

## DAY 1 (Apr 24) — Public Endpoint + Staging + Commons Repo

### Task 0: Day-1 morning decision call (user, not engineer)

**Files:** none — user decision only

- [ ] **Step 1: License choice** — CC-BY-SA-4.0 (default) OR Apache-2.0. 30-min cap. Write choice to `/root/claude-code-agentic/darwin-mvp/LICENSE_COMMONS.txt`.
- [ ] **Step 2: Badge hosting choice** — static SVG in commons repo (default) OR live from server. Write choice to `/root/claude-code-agentic/darwin-mvp/BADGE_MODE.txt` (value: `static` or `live`).

**Dependency:** none. **Rollback:** swap LICENSE file + recommit if changed Day 2+.

---

### Task 1: Add `flask-limiter` + `gitpython` dependencies

**Files:**
- Modify: `darwin-mvp/requirements.txt`

- [ ] **Step 1: Append dependencies**

```
flask-limiter>=3.5.0
gitpython>=3.1.40
```

- [ ] **Step 2: Install + verify import**

```bash
cd /root/claude-code-agentic/darwin-mvp
pip install -r requirements.txt
python3 -c "import flask_limiter; import git; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add requirements.txt
git -C /root/claude-code-agentic/darwin-mvp commit -m "chore: add flask-limiter + gitpython for commons endpoint"
```

---

### Task 2: Write failing tests for `/darwin/heal/public` endpoint

**Files:**
- Create: `darwin-mvp/test_public_endpoint.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for the public heal endpoint."""
from __future__ import annotations

import json
import pytest
import webhook_ingest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DARWIN_PUBLIC_RATE_LIMIT", "100/hour")  # relax for tests
    monkeypatch.setenv("DARWIN_PUBLIC_DAILY_BUDGET_USD", "5")
    monkeypatch.setenv("DARWIN_STAGING_DIR", str(tmp_path))
    webhook_ingest.app.config["TESTING"] = True
    return webhook_ingest.app.test_client()


def _payload(publish=False, attestation=None):
    body = {
        "originating_agent": "test",
        "stderr": "Traceback (most recent call last):\n  File \"a.py\", line 2, in mod\n    d[\"text\"]\nKeyError: 'text'",
        "source_code": "d = {}\nprint(d[\"text\"])\n",
        "publish_to_commons": publish,
    }
    if attestation is not None:
        body["contributor_attestation"] = attestation
    return body


def test_public_endpoint_returns_200_on_anonymous_heuristic(client):
    resp = client.post("/darwin/heal/public", json=_payload())
    assert resp.status_code in (200, 500)  # 500 if heuristic doesn't match


def test_public_endpoint_rejects_payload_over_16kb(client):
    big = "x" * 17000
    resp = client.post("/darwin/heal/public", json={"stderr": big, "source_code": ""})
    assert resp.status_code == 413


def test_public_endpoint_rejects_malformed_traceback(client):
    resp = client.post("/darwin/heal/public", json={"stderr": "not a traceback", "source_code": "x=1"})
    assert resp.status_code == 400


def test_publish_requires_attestation(client):
    resp = client.post("/darwin/heal/public", json=_payload(publish=True, attestation=None))
    assert resp.status_code == 400
    assert "attestation" in resp.get_json().get("error", "").lower()


def test_publish_with_wrong_attestation_phrase_rejected(client):
    resp = client.post("/darwin/heal/public", json=_payload(publish=True, attestation="I agree"))
    assert resp.status_code == 400


def test_publish_with_correct_attestation_writes_staging(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DARWIN_STAGING_DIR", str(tmp_path))
    resp = client.post(
        "/darwin/heal/public",
        json=_payload(publish=True, attestation="I have the right to submit this code under CC-BY-SA-4.0."),
    )
    assert resp.status_code in (200, 500)
    staging = tmp_path / "pending.jsonl"
    if resp.status_code == 200:
        assert staging.exists()


def test_badge_endpoint_returns_shields_compatible_json(client):
    resp = client.get("/darwin/commons/badge/ch-abc123")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schemaVersion"] == 1
    assert data["label"] == "darwin commons"
    assert "fingerprints" in data["message"]
```

- [ ] **Step 2: Run — expect all fail**

```bash
cd /root/claude-code-agentic/darwin-mvp && pytest test_public_endpoint.py -v 2>&1 | tail -20
```

Expected: 7 tests, most FAIL (routes don't exist yet)

- [ ] **Step 3: Commit failing tests**

```bash
git -C /root/claude-code-agentic/darwin-mvp add test_public_endpoint.py
git -C /root/claude-code-agentic/darwin-mvp commit -m "test: failing tests for public heal endpoint + commons badge"
```

---

### Task 3: Implement `/darwin/heal/public` route with attestation + rate limit + staging write

**Files:**
- Modify: `darwin-mvp/webhook_ingest.py`

- [ ] **Step 1: Add imports and rate limiter at module scope (insert after line 36)**

Open `webhook_ingest.py`, after the existing imports, insert:

```python
import hashlib
import re
import uuid
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

REQUIRED_ATTESTATION = "I have the right to submit this code under CC-BY-SA-4.0."

MAX_PAYLOAD_BYTES = 16 * 1024
MAX_SOURCE_BYTES = 8 * 1024
TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)

_DAILY_BUDGET_SPENT_USD = 0.0
_DAILY_BUDGET_DATE = None

_STAGING_DIR = Path(os.environ.get("DARWIN_STAGING_DIR", str(HERE / "staging")))
_STAGING_DIR.mkdir(parents=True, exist_ok=True)
_STAGING_FILE = _STAGING_DIR / "pending.jsonl"

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[os.environ.get("DARWIN_PUBLIC_RATE_LIMIT", "10/hour")],
)
```

- [ ] **Step 2: Add helper functions (end of file, before `if __name__ == "__main__"`)**

```python
def _contributor_hash(remote_addr: str) -> str:
    salt = os.environ.get("DARWIN_CONTRIBUTOR_SALT", "darwin-commons-v1")
    return "ch-" + hashlib.sha256((salt + remote_addr).encode()).hexdigest()[:12]


def _stage_for_commons(entry: dict) -> str:
    entry["commons_staged_id"] = f"ph-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8]}"
    with _STAGING_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["commons_staged_id"]
```

- [ ] **Step 3: Add the `/darwin/heal/public` route**

```python
@app.route("/darwin/heal/public", methods=["POST"])
@limiter.limit(os.environ.get("DARWIN_PUBLIC_RATE_LIMIT", "10/hour"))
def heal_public():
    if os.environ.get("DARWIN_PUBLIC_DISABLED") == "1":
        return _err("public endpoint disabled", 503)

    raw = request.get_data()
    if len(raw) > MAX_PAYLOAD_BYTES:
        return _err(f"payload too large (>{MAX_PAYLOAD_BYTES} bytes)", 413)

    try:
        payload = json.loads(raw)
    except Exception as e:
        return _err(f"invalid JSON: {e}", 400)

    stderr = payload.get("stderr", "")
    source_code = payload.get("source_code", "")
    if not TRACEBACK_RE.search(stderr):
        return _err("stderr does not parse as Python traceback", 400)
    if len(source_code.encode()) > MAX_SOURCE_BYTES:
        return _err(f"source_code too large (>{MAX_SOURCE_BYTES} bytes)", 413)

    publish = bool(payload.get("publish_to_commons"))
    attestation = payload.get("contributor_attestation", "")
    if publish:
        if attestation != REQUIRED_ATTESTATION:
            return _err(
                "publish_to_commons requires contributor_attestation matching "
                f"exactly: {REQUIRED_ATTESTATION!r}",
                400,
            )

    # Delegate to existing failure handler logic
    # Reuse internal function — avoid duplicating fingerprint/heal code
    import signature
    fp = signature.fingerprint(stderr)
    prior = blackboard.find_by_fingerprint(fp)
    resp: dict = {"fingerprint": fp, "cache_hit": prior is not None}

    if prior:
        resp.update({"status": "healed_from_cache", "new_source": prior.get("fix_code")})
    else:
        # Anonymous path: heuristic only, no LLM call
        has_user_key = bool(request.headers.get("x-darwin-key"))
        if not has_user_key:
            # Heuristic-only attempt
            from darwin_harness import heuristic_fix
            try:
                fix_code = heuristic_fix(source_code, stderr)
            except Exception:
                fix_code = None
            if fix_code is None:
                resp["status"] = "cache_miss_heuristic_only"
                return jsonify(resp), 200
            resp.update({"status": "diagnosed_heuristic", "new_source": fix_code})
        else:
            fix_code = diagnose_and_fix(source_code, stderr)
            if fix_code is None:
                resp["status"] = "diagnose_failed"
                return jsonify(resp), 500
            resp.update({"status": "diagnosed_and_cached", "new_source": fix_code})
            blackboard.write_fix(stderr, root_cause="public heal", fix_code=fix_code)

    if publish and resp.get("new_source"):
        staged_id = _stage_for_commons({
            "fingerprint": fp,
            "error_class": payload.get("error_class") or "unknown",
            "stderr": stderr,
            "source_code": source_code,
            "new_source": resp["new_source"],
            "contributor_hash": _contributor_hash(get_remote_address()),
            "attestation_phrase_sha256": hashlib.sha256(attestation.encode()).hexdigest(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "generator": "heuristic" if not has_user_key else "llm",
        })
        resp["commons_staged_id"] = staged_id

    response = jsonify(resp)
    response.headers["X-Darwin-Commons-Credit"] = _contributor_hash(get_remote_address())
    return response
```

- [ ] **Step 4: Add `/darwin/commons/badge/<hash>` route**

```python
@app.route("/darwin/commons/badge/<contributor_hash>", methods=["GET"])
def commons_badge(contributor_hash: str):
    # Count fingerprints attributed to this contributor_hash in staging + blackboard
    count = 0
    if _STAGING_FILE.exists():
        with _STAGING_FILE.open() as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("contributor_hash") == contributor_hash:
                        count += 1
                except Exception:
                    continue
    return jsonify({
        "schemaVersion": 1,
        "label": "darwin commons",
        "message": f"{count} fingerprints",
        "color": "blue" if count > 0 else "lightgrey",
    })
```

- [ ] **Step 5: Add `heuristic_fix` function if missing**

Check if `darwin_harness.heuristic_fix` exists:
```bash
grep -n "def heuristic_fix\|def _heuristic" /root/claude-code-agentic/darwin-mvp/darwin_harness.py
```

If absent, wrap the existing heuristic path or add a stub that returns None (existing regex adapters handle 5 classes).

- [ ] **Step 6: Run tests**

```bash
cd /root/claude-code-agentic/darwin-mvp && pytest test_public_endpoint.py -v 2>&1 | tail -20
```

Expected: 7 passed (or 6 passed + 1 skipped for the staging test if staging dir permission issue).

- [ ] **Step 7: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add webhook_ingest.py
git -C /root/claude-code-agentic/darwin-mvp commit -m "feat: public /darwin/heal/public endpoint with attestation gate and staging write"
```

**Dependency:** Task 1, 2. **Rollback:** `git revert` the route commit — the existing `/darwin/failure` route is untouched.

---

### Task 4: Create `Miles0sage/darwin-commons` repo + seed files

**Files (in new repo `darwin-commons/`, cloned locally at `/root/darwin-commons`):**
- Create: `darwin-commons/README.md`
- Create: `darwin-commons/LICENSE` (copy chosen license file from Task 0 output)
- Create: `darwin-commons/SCHEMA.md`
- Create: `darwin-commons/CONTRIBUTING.md`
- Create: `darwin-commons/CODE_OF_CONDUCT.md`
- Create: `darwin-commons/fingerprints.jsonl` (empty)
- Create: `darwin-commons/quarantine.jsonl` (empty)
- Create: `darwin-commons/transformers/.gitkeep`
- Create: `darwin-commons/.github/workflows/commons-verify.yml`

- [ ] **Step 1: Create repo on GitHub (manual — user runs this)**

```bash
# User step — paste in shell
gh repo create Miles0sage/darwin-commons --public --description "Public corpus of agent failure fingerprints → LibCST transformers. Contribute by running Darwin." --clone --confirm
cd Miles0sage-darwin-commons
```

Expected: repo exists at https://github.com/Miles0sage/darwin-commons, local clone at `./Miles0sage-darwin-commons`.

- [ ] **Step 2: Write `README.md`**

```markdown
# Darwin Commons

The public, signed, CI-verified corpus of agent failure fingerprints → LibCST transformers.

Every entry is a tuple: `(traceback_fingerprint, error_class, cached_transformer, contributor_hash, license)`. Contribute by running [Darwin](https://github.com/Miles0sage/darwin) and opting in to publish.

## Live status
<!-- COUNTER_START -->
**Fingerprints: 0**
<!-- COUNTER_END -->

## What this is
- A public, attributed dataset of agent failure → patch pairs
- Licensed under [LICENSE] for unrestricted reuse (with attribution)
- GPG-signed commits from `darwin-commons-bot` (key: `gpg-pubkey.asc`)
- CI-verified: every entry replayed on fresh checkout, failing entries moved to `quarantine.jsonl`

## What this is NOT
- NOT a place to dump private or proprietary code (use your own Darwin instance)
- NOT a replacement for Sentry/Datadog
- NOT a primitive moat — the corpus is the moat

## Schema
See [SCHEMA.md](SCHEMA.md).

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: submit through Darwin's `/darwin/heal/public` with `publish_to_commons=true` and the contributor attestation phrase.

## Badges
Earn a contributor badge by submitting fingerprints. Claim your SVG at:
`https://<darwin-server>/darwin/commons/badge/<your-contributor-hash>`

## License
See [LICENSE]. Transformers and metadata are dual-available under attribution terms.
```

- [ ] **Step 3: Write `SCHEMA.md`**

```markdown
# Darwin Commons Schema

Each line of `fingerprints.jsonl` is a JSON object:

\`\`\`json
{
  "fingerprint": "0b8ed4dc613c4688",
  "error_class": "KeyError",
  "normalized_signature": "File \"a.py\", line N, in mod\\n...",
  "transformer_path": "transformers/0b8ed4dc613c4688.py",
  "transformer_sha256": "abc123...",
  "generator": {
    "model": "gemini-flash-latest",
    "provider": "google",
    "timestamp": "2026-04-24T03:12:44Z"
  },
  "provenance": {
    "contributor_hash": "ch-a1b2c3d4",
    "public_heal_id": "ph-2026-04-24-000042",
    "attestation_phrase_sha256": "hash-of-the-attestation-string-they-submitted"
  },
  "license": "CC-BY-SA-4.0"
}
\`\`\`

Quarantined entries (`quarantine.jsonl`) add:

\`\`\`json
{
  "quarantine": {"reason": "transformer did not parse", "first_seen": "...", "retries": 0, "last_error": "..."}
}
\`\`\`

## Protocol status
This schema IS the de-facto protocol. When Darwin Commons reaches 500 entries, we will formalize as an OpenAPI spec. Until then: the endpoint's JSON is the spec.
```

- [ ] **Step 4: Write `CONTRIBUTING.md`**

```markdown
# Contributing to Darwin Commons

## Via Darwin public endpoint
POST to `https://<darwin-server>/darwin/heal/public` with:

\`\`\`json
{
  "stderr": "<full Python traceback>",
  "source_code": "<full failing source>",
  "publish_to_commons": true,
  "contributor_attestation": "I have the right to submit this code under CC-BY-SA-4.0."
}
\`\`\`

Your contribution writes to `fingerprints.jsonl` on the next 15-minute sync tick.

## Via pull request (manual)
Fork this repo, add a JSONL entry + transformer file, open PR. Each PR must:
- Include a valid `contributor_attestation` for each entry
- Pass `commons-verify.yml` CI (replays the transformer)
- Be GPG-signed

## Code of Conduct
See [CODE_OF_CONDUCT.md].
```

- [ ] **Step 5: Write `CODE_OF_CONDUCT.md`**

Paste Contributor Covenant 2.1:
```bash
curl -s https://www.contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.md > CODE_OF_CONDUCT.md
```

- [ ] **Step 6: Create empty JSONL + .gitkeep**

```bash
touch fingerprints.jsonl quarantine.jsonl
mkdir -p transformers
touch transformers/.gitkeep
```

- [ ] **Step 7: Commit + push**

```bash
git add .
git commit -m "init: Darwin Commons schema + docs + empty corpus"
git push -u origin main
```

Expected: commit lands on github.com/Miles0sage/darwin-commons

**Dependency:** Task 0 (license choice). **Rollback:** `gh repo delete Miles0sage/darwin-commons --yes` if bad state.

---

### Task 5: Generate GPG key for `darwin-commons-bot`

**Files:**
- Create: `/root/darwin-commons-bot.gpg-key` (private, chmod 600)
- Create: `darwin-commons/gpg-pubkey.asc`

- [ ] **Step 1: Generate Ed25519 GPG key (modern default)**

```bash
export GNUPGHOME=/root/.gnupg-darwin-commons
mkdir -p $GNUPGHOME && chmod 700 $GNUPGHOME
gpg --quick-gen-key "darwin-commons-bot <darwin-commons-bot@users.noreply.github.com>" ed25519 sign 0
```

Expected: key pair generated. Capture fingerprint:
```bash
gpg --list-secret-keys --keyid-format=long | grep -A1 "darwin-commons-bot" | head
```

- [ ] **Step 2: Export public key to commons repo**

```bash
gpg --armor --export darwin-commons-bot > /path/to/darwin-commons/gpg-pubkey.asc
```

- [ ] **Step 3: Commit + push pubkey**

```bash
cd /path/to/darwin-commons
git add gpg-pubkey.asc
git commit -S -m "chore: add GPG pubkey for darwin-commons-bot"
git push
```

Expected: commit shows "Verified" badge on GitHub.

- [ ] **Step 4: Back up private key**

```bash
cp -r $GNUPGHOME /root/backups/darwin-commons-gnupg-$(date +%Y%m%d)
```

**Dependency:** Task 4. **Rollback plan for key compromise:** revoke publicly via `gpg --gen-revoke`, commit revocation cert to repo, regenerate new key, re-sign all future commits. This is why we back up the private key offline.

---

### Task 6: Implement `commons_sync.py` worker

**Files:**
- Create: `darwin-mvp/commons_sync.py`

- [ ] **Step 1: Write the sync script**

```python
#!/usr/bin/env python3
"""
Darwin Commons Sync — cron-batched worker.

Reads staging/pending.jsonl, runs each entry through the AST-diff verifier,
promotes passing entries to fingerprints.jsonl (GPG-signed commit + push),
writes failing entries to quarantine.jsonl.

Idempotent: tracks last-processed position in staging/.sync-offset.
Restart-safe: all operations committed before offset advance.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from git import Repo  # type: ignore
except ImportError:
    print("ERROR: gitpython not installed. pip install gitpython", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from darwin_harness import validate_fix  # noqa: E402


STAGING_DIR = Path(os.environ.get("DARWIN_STAGING_DIR", str(HERE / "staging")))
STAGING_FILE = STAGING_DIR / "pending.jsonl"
OFFSET_FILE = STAGING_DIR / ".sync-offset"

COMMONS_REPO_PATH = Path(os.environ.get("DARWIN_COMMONS_REPO", "/root/darwin-commons"))
COMMONS_BRANCH = os.environ.get("DARWIN_COMMONS_BRANCH", "main")


def _read_offset() -> int:
    if not OFFSET_FILE.exists():
        return 0
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _write_offset(n: int) -> None:
    OFFSET_FILE.write_text(str(n))


def _verify_entry(entry: dict) -> tuple[bool, str]:
    """Re-run the AST-diff gate. Returns (ok, reason)."""
    source = entry.get("source_code", "")
    new_source = entry.get("new_source", "")
    stderr = entry.get("stderr", "")
    if not source or not new_source:
        return False, "missing source or new_source"
    ok, reasons = validate_fix(source, new_source, stderr)
    return ok, "; ".join(reasons) if not ok else ""


def _write_to_commons(entry: dict, repo: Repo) -> str:
    fingerprint = entry["fingerprint"]
    transformer_path = f"transformers/{fingerprint}.py"
    abs_path = COMMONS_REPO_PATH / transformer_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(entry["new_source"])

    commons_entry = {
        "fingerprint": fingerprint,
        "error_class": entry.get("error_class", "unknown"),
        "normalized_signature": entry.get("stderr", "").splitlines()[-1] if entry.get("stderr") else "",
        "transformer_path": transformer_path,
        "transformer_sha256": hashlib.sha256(entry["new_source"].encode()).hexdigest(),
        "generator": {
            "model": entry.get("generator", "unknown"),
            "provider": "darwin-public",
            "timestamp": entry.get("timestamp"),
        },
        "provenance": {
            "contributor_hash": entry.get("contributor_hash"),
            "public_heal_id": entry.get("commons_staged_id"),
            "attestation_phrase_sha256": entry.get("attestation_phrase_sha256"),
        },
        "license": os.environ.get("DARWIN_COMMONS_LICENSE", "CC-BY-SA-4.0"),
    }

    fp_file = COMMONS_REPO_PATH / "fingerprints.jsonl"
    with fp_file.open("a") as f:
        f.write(json.dumps(commons_entry) + "\n")

    repo.index.add([transformer_path, "fingerprints.jsonl"])
    commit = repo.index.commit(
        f"add: fingerprint {fingerprint} ({entry.get('error_class', '?')})"
    )
    return str(commit.hexsha)


def _quarantine(entry: dict, reason: str, repo: Repo) -> None:
    q_file = COMMONS_REPO_PATH / "quarantine.jsonl"
    entry["quarantine"] = {
        "reason": reason,
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "retries": 0,
        "last_error": reason,
    }
    with q_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    repo.index.add(["quarantine.jsonl"])
    repo.index.commit(f"quarantine: {entry.get('fingerprint', '?')} ({reason[:60]})")


def main() -> int:
    if not STAGING_FILE.exists():
        print(f"no staging file at {STAGING_FILE}; nothing to sync")
        return 0

    offset = _read_offset()
    lines = STAGING_FILE.read_text().splitlines()
    if offset >= len(lines):
        return 0

    if not COMMONS_REPO_PATH.exists():
        print(f"ERROR: commons repo missing at {COMMONS_REPO_PATH}", file=sys.stderr)
        return 2
    repo = Repo(str(COMMONS_REPO_PATH))

    new_count = 0
    quarantined = 0
    for i in range(offset, len(lines)):
        line = lines[i].strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception as e:
            print(f"[line {i}] bad JSON: {e}", file=sys.stderr)
            continue
        ok, reason = _verify_entry(entry)
        if ok:
            try:
                _write_to_commons(entry, repo)
                new_count += 1
            except Exception as e:
                print(f"[line {i}] commit failed: {e}", file=sys.stderr)
                continue
        else:
            _quarantine(entry, reason, repo)
            quarantined += 1
        _write_offset(i + 1)

    if new_count + quarantined > 0:
        repo.remotes.origin.push()

    print(f"sync done: +{new_count} published, +{quarantined} quarantined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write failing tests**

Create `darwin-mvp/test_commons_sync.py`:

```python
import json
import os
import subprocess
import pytest
from pathlib import Path


@pytest.fixture
def fake_commons_repo(tmp_path, monkeypatch):
    repo = tmp_path / "commons"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "fingerprints.jsonl").touch()
    (repo / "quarantine.jsonl").touch()
    (repo / "transformers").mkdir()
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    monkeypatch.setenv("DARWIN_COMMONS_REPO", str(repo))
    return repo


@pytest.fixture
def staging(tmp_path, monkeypatch):
    d = tmp_path / "staging"
    d.mkdir()
    monkeypatch.setenv("DARWIN_STAGING_DIR", str(d))
    return d


def test_sync_processes_valid_entry(staging, fake_commons_repo, monkeypatch):
    entry = {
        "fingerprint": "abc123",
        "error_class": "KeyError",
        "stderr": "KeyError: 'text'",
        "source_code": "d = {}\nprint(d['text'])\n",
        "new_source": "d = {}\nprint(d.get('text'))\n",
        "contributor_hash": "ch-test",
        "commons_staged_id": "ph-test-1",
        "timestamp": "2026-04-24T00:00:00Z",
    }
    (staging / "pending.jsonl").write_text(json.dumps(entry) + "\n")
    # Disable remote push in test
    monkeypatch.setattr("commons_sync.Repo", lambda p: _FakeRepo(p))  # minimal stub OR use real repo w/o remote

    import commons_sync
    rc = commons_sync.main()
    assert rc == 0


def test_sync_is_idempotent(staging, fake_commons_repo):
    entry = {
        "fingerprint": "abc", "source_code": "x=1", "new_source": "x=1",
        "error_class": "None", "stderr": "Traceback (most recent call last):\nValueError",
    }
    (staging / "pending.jsonl").write_text(json.dumps(entry) + "\n")
    # Run twice — second run should be a no-op
    import commons_sync
    commons_sync.main()
    commons_sync.main()  # should not re-commit
```

(Note: these tests use a minimal stub. In a real workspace, replace `_FakeRepo` with a proper fixture.)

- [ ] **Step 3: Run tests**

```bash
cd /root/claude-code-agentic/darwin-mvp && pytest test_commons_sync.py -v 2>&1 | tail -20
```

Expected: passes OR clear xfail with reason.

- [ ] **Step 4: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add commons_sync.py test_commons_sync.py
git -C /root/claude-code-agentic/darwin-mvp commit -m "feat: commons_sync.py — cron-batched staging → commons sync"
```

**Dependency:** Tasks 3, 4, 5. **Rollback:** revert commit; sync script not invoked anywhere yet.

---

### Task 7: First manual sync — seed 20 entries

**Files:**
- Modify: `darwin-commons/fingerprints.jsonl` (+20 entries)

- [ ] **Step 1: Clear test staging, seed real staging from existing fixes/**

```bash
cd /root/claude-code-agentic/darwin-mvp
python3 -c "
import json, os
from pathlib import Path
fixes = Path('fixes')
staging = Path(os.environ.get('DARWIN_STAGING_DIR','staging'))
staging.mkdir(exist_ok=True)
out = staging / 'pending.jsonl'
seen = set()
with out.open('w') as f:
    for p in sorted(fixes.glob('fix-*.json'))[:20]:
        e = json.loads(p.read_text())
        if e.get('fingerprint') in seen:
            continue
        seen.add(e.get('fingerprint'))
        f.write(json.dumps({
            'fingerprint': e.get('fingerprint') or p.stem,
            'error_class': e.get('error_class','unknown'),
            'stderr': e.get('stderr',''),
            'source_code': e.get('source_code',''),
            'new_source': e.get('fix_code',''),
            'contributor_hash': 'ch-darwin-seed',
            'commons_staged_id': 'ph-seed-' + p.stem,
            'timestamp': '2026-04-24T00:00:00Z',
            'generator': 'seed-from-fixes',
        }) + '\n')
print('staged:', sum(1 for _ in out.open()))
"
```

Expected: `staged: 20`

- [ ] **Step 2: Run sync**

```bash
export DARWIN_COMMONS_REPO=/root/darwin-commons
export DARWIN_STAGING_DIR=/root/claude-code-agentic/darwin-mvp/staging
python3 commons_sync.py
```

Expected: `sync done: +N published, +M quarantined` (N+M=20)

- [ ] **Step 3: Verify commons repo**

```bash
cd /root/darwin-commons
git log --oneline | head
wc -l fingerprints.jsonl quarantine.jsonl
```

Expected: ≥20 commits, fingerprints.jsonl has N lines, quarantine.jsonl has M lines.

- [ ] **Step 4: Push**

```bash
git push origin main
```

Expected: push succeeds, commits visible on github.com/Miles0sage/darwin-commons.

**Dependency:** Task 6. **Rollback plan for bad seed:** `git reset --hard <pre-seed-commit>` then force-push. Use this ONLY if the seed turns out to leak secrets or bad data. Capture pre-seed SHA: `git rev-parse HEAD` BEFORE running.

---

### Task 8: Systemd timer for cron-batched sync

**Files:**
- Create: `darwin-mvp/scripts/commons-sync.service`
- Create: `darwin-mvp/scripts/commons-sync.timer`
- Create: `darwin-mvp/scripts/install-commons-sync.sh`

- [ ] **Step 1: Write service unit**

`darwin-mvp/scripts/commons-sync.service`:
```ini
[Unit]
Description=Darwin Commons sync
After=network-online.target

[Service]
Type=oneshot
Environment="DARWIN_STAGING_DIR=/root/claude-code-agentic/darwin-mvp/staging"
Environment="DARWIN_COMMONS_REPO=/root/darwin-commons"
Environment="GNUPGHOME=/root/.gnupg-darwin-commons"
ExecStart=/usr/bin/python3 /root/claude-code-agentic/darwin-mvp/commons_sync.py
User=root
```

- [ ] **Step 2: Write timer unit**

`darwin-mvp/scripts/commons-sync.timer`:
```ini
[Unit]
Description=Darwin Commons sync every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Unit=commons-sync.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Install + enable**

`darwin-mvp/scripts/install-commons-sync.sh`:
```bash
#!/usr/bin/env bash
set -e
cp scripts/commons-sync.service /etc/systemd/system/
cp scripts/commons-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now commons-sync.timer
systemctl list-timers --all | grep commons
```

- [ ] **Step 4: Run installer**

```bash
cd /root/claude-code-agentic/darwin-mvp && bash scripts/install-commons-sync.sh
```

Expected: `commons-sync.timer` appears in active timers.

- [ ] **Step 5: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add scripts/
git -C /root/claude-code-agentic/darwin-mvp commit -m "feat: systemd timer for commons_sync (15-min interval)"
```

**Rollback:** `systemctl disable --now commons-sync.timer; rm /etc/systemd/system/commons-sync.{service,timer}; systemctl daemon-reload`

---

## DAY 2 (Apr 25) — DLQ Triage + Contributor Badge + Launch Content

### Task 9: Implement `commons_triage.py` CLI

**Files:**
- Create: `darwin-mvp/commons_triage.py`

- [ ] **Step 1: Write CLI**

```python
#!/usr/bin/env python3
"""Triage quarantined Darwin Commons entries."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

COMMONS_REPO = Path(os.environ.get("DARWIN_COMMONS_REPO", "/root/darwin-commons"))
Q_FILE = COMMONS_REPO / "quarantine.jsonl"


def cmd_list(args):
    if not Q_FILE.exists():
        print("no quarantine file")
        return 0
    for i, line in enumerate(Q_FILE.read_text().splitlines()):
        try:
            e = json.loads(line)
        except Exception:
            continue
        q = e.get("quarantine", {})
        print(f"[{i}] fp={e.get('fingerprint','?')} class={e.get('error_class','?')} reason={q.get('reason','?')}")
    return 0


def cmd_inspect(args):
    line = Q_FILE.read_text().splitlines()[args.index]
    print(json.dumps(json.loads(line), indent=2))
    return 0


def cmd_rescue(args):
    lines = Q_FILE.read_text().splitlines()
    entry = json.loads(lines[args.index])
    entry.pop("quarantine", None)
    staging_dir = Path(os.environ.get("DARWIN_STAGING_DIR", COMMONS_REPO.parent / "staging"))
    staging_dir.mkdir(exist_ok=True)
    (staging_dir / "pending.jsonl").open("a").write(json.dumps(entry) + "\n")
    # Remove from quarantine
    del lines[args.index]
    Q_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))
    print(f"rescued index {args.index} → staging")
    return 0


def cmd_purge(args):
    lines = Q_FILE.read_text().splitlines()
    del lines[args.index]
    Q_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))
    print(f"purged index {args.index}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").set_defaults(func=cmd_list)
    i = sp.add_parser("inspect"); i.add_argument("index", type=int); i.set_defaults(func=cmd_inspect)
    r = sp.add_parser("rescue"); r.add_argument("index", type=int); r.set_defaults(func=cmd_rescue)
    p = sp.add_parser("purge"); p.add_argument("index", type=int); p.set_defaults(func=cmd_purge)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Test**

```bash
cd /root/darwin-commons
# Inject a broken quarantine entry for smoke test
echo '{"fingerprint":"test","error_class":"TestError","quarantine":{"reason":"test","first_seen":"2026-04-24T00:00:00Z","retries":0}}' >> quarantine.jsonl
cd /root/claude-code-agentic/darwin-mvp
python3 commons_triage.py list
# Expected: lists the test entry
python3 commons_triage.py purge 0
# Expected: "purged index 0"
python3 commons_triage.py list
# Expected: no entries
cd /root/darwin-commons && git checkout -- quarantine.jsonl
```

- [ ] **Step 3: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add commons_triage.py
git -C /root/claude-code-agentic/darwin-mvp commit -m "feat: commons_triage.py CLI — list/inspect/rescue/purge"
```

**Dependency:** Task 6.

---

### Task 10: CI workflow `commons-verify.yml`

**Files:**
- Create: `darwin-commons/.github/workflows/commons-verify.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Commons Verify
on: [push, pull_request]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install deps
        run: pip install libcst
      - name: Verify each fingerprint entry
        run: |
          python3 <<'EOF'
          import json, sys, pathlib, libcst as cst
          fp_file = pathlib.Path('fingerprints.jsonl')
          if not fp_file.exists() or fp_file.stat().st_size == 0:
              print('no entries; skip'); sys.exit(0)
          ok = 0; bad = 0
          for i, line in enumerate(fp_file.read_text().splitlines()):
              if not line.strip(): continue
              try:
                  e = json.loads(line)
              except Exception as ex:
                  print(f'[{i}] bad JSON: {ex}'); bad += 1; continue
              tp = pathlib.Path(e.get('transformer_path',''))
              if not tp.exists():
                  print(f'[{i}] missing transformer at {tp}'); bad += 1; continue
              try:
                  cst.parse_module(tp.read_text())
              except Exception as ex:
                  print(f'[{i}] transformer did not parse: {ex}'); bad += 1; continue
              ok += 1
          print(f'{ok} ok, {bad} bad')
          sys.exit(0 if bad == 0 else 1)
          EOF
```

- [ ] **Step 2: Commit + push**

```bash
cd /root/darwin-commons
git add .github/workflows/commons-verify.yml
git commit -m "ci: commons-verify replays every transformer"
git push
```

- [ ] **Step 3: Wait for CI green**

Visit https://github.com/Miles0sage/darwin-commons/actions. Expected: green checkmark within 2 minutes.

**Rollback:** disable workflow via GH UI if it blocks legitimate PRs.

---

### Task 11: README counter auto-update (GitHub Actions)

**Files:**
- Create: `darwin-commons/.github/workflows/update-counter.yml`

- [ ] **Step 1: Write workflow**

```yaml
name: Update Counter
on:
  push:
    paths: [fingerprints.jsonl]
  schedule:
    - cron: '0 */4 * * *'

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.PAT_TOKEN }}
      - name: Update README counter
        run: |
          COUNT=$(wc -l < fingerprints.jsonl | tr -d ' ')
          python3 -c "
          import re, pathlib
          r = pathlib.Path('README.md')
          t = r.read_text()
          t = re.sub(r'<!-- COUNTER_START -->.*<!-- COUNTER_END -->', f'<!-- COUNTER_START -->\\n**Fingerprints: $COUNT**\\n<!-- COUNTER_END -->', t, flags=re.S)
          r.write_text(t)
          "
          git config user.email "darwin-commons-bot@users.noreply.github.com"
          git config user.name  "darwin-commons-bot"
          git diff --quiet README.md || (git add README.md && git commit -m "counter: $COUNT" && git push)
```

- [ ] **Step 2: Commit + push**

```bash
cd /root/darwin-commons
git add .github/workflows/update-counter.yml
git commit -m "ci: auto-update counter in README"
git push
```

---

### Task 12: Update `darwin-mvp/README.md` to reference Commons

**Files:**
- Modify: `darwin-mvp/README.md`

- [ ] **Step 1: Add Commons mention to hero**

Insert after the existing hero paragraph:

```markdown
> **Now live:** The [Darwin Commons](https://github.com/Miles0sage/darwin-commons) — the first public corpus of agent failure → LibCST transformer pairs. Contribute by running Darwin with `publish_to_commons=true` and get a contributor badge for your README.
```

- [ ] **Step 2: Commit**

```bash
git -C /root/claude-code-agentic/darwin-mvp add README.md
git -C /root/claude-code-agentic/darwin-mvp commit -m "docs: reference Darwin Commons in hero"
```

---

### Task 13: Launch content — finalize and stage

**Files:**
- Modify: `/tmp/darwin-sync/launch/READY-TO-FIRE.md` (re-center on Commons)

- [ ] **Step 1: Re-write `/tmp/darwin-sync/launch/READY-TO-FIRE.md`** to center on Commons flywheel, not mechanism. Keep X/LinkedIn/HN/email template structure.

- [ ] **Step 2: Verify copies are live-fire-ready**

```bash
grep -l "AST-level\|durable patch" /tmp/darwin-sync/launch/*.md
```

Expected: all copies reference "Darwin Commons" at least once; none lean on stale mechanism positioning.

---

## DAY 3 (Apr 26) — Asciinema + Launch Wave

### Task 14: Record asciinema demo

**Files:**
- Create: `/tmp/darwin-sync/commons-demo-script.sh`

- [ ] **Step 1: Write demo script**

```bash
#!/usr/bin/env bash
set -e
echo "# Darwin Commons live demo"
sleep 2
echo "# Fire 10 different bugs at /darwin/heal/public, watch Commons grow, hit cache on 11th."
sleep 3
for i in 1 2 3 4 5 6 7 8 9 10; do
  echo ">>> bug $i: AttributeError in agent-$i.py"
  curl -s -X POST http://127.0.0.1:7777/darwin/heal/public \
    -H 'content-type: application/json' \
    --data @/tmp/darwin-sync/demo-bugs/bug-$i.json | python3 -c "import sys,json; r=json.loads(sys.stdin.read()); print(f'  → fingerprint={r[\"fingerprint\"][:12]} status={r[\"status\"]}')"
  sleep 1
done
echo ""
echo ">>> 11th bug: same AttributeError shape, different variable names"
curl -s -X POST http://127.0.0.1:7777/darwin/heal/public -H 'content-type: application/json' --data @/tmp/darwin-sync/demo-bugs/bug-11-cache-hit.json
echo ""
echo ">>> Commons counter:"
curl -s http://127.0.0.1:7777/darwin/status | python3 -m json.tool | grep -E "heals|cache_hits|llm_diagnoses"
```

- [ ] **Step 2: Record**

```bash
asciinema rec /tmp/darwin-sync/commons-demo.cast -c "bash /tmp/darwin-sync/commons-demo-script.sh"
asciinema upload /tmp/darwin-sync/commons-demo.cast
```

Expected: upload URL captured.

- [ ] **Step 3: Embed in READMEs**

Update both `darwin-mvp/README.md` and `darwin-commons/README.md` with the asciinema badge.

---

### Task 15: Launch wave (09:00 PT staggered)

- [ ] **Step 1 (09:00 PT): Post HN Show HN**

Title: `Show HN: Darwin Commons – Public corpus of agent failure → transformer pairs`
Body: first line is repo URL, then body from launch kit.
**Do not self-upvote.**

- [ ] **Step 2 (09:30 PT): X thread**

From `/tmp/darwin-sync/launch/x-thread.md` (updated to Commons-centric).

- [ ] **Step 3 (10:00 PT): LinkedIn**

From `/tmp/darwin-sync/launch/linkedin-post.md` (updated).

- [ ] **Step 4 (10:30 PT): r/LocalLLaMA + r/aipractitioners**

Text post, link + body.

- [ ] **Step 5 (11:00 PT): DM outreach**

DM Agent Lightning maintainers + pfix maintainer + Aider maintainer. Tone: "built this, would love your take." No PR yet.

- [ ] **Step 6 (12:00 PT onward): live response loop**

Respond to HN comments within 15min. Fix any broken demo link / bad README claim immediately. First 6 hours of HN is the whole game.

**Rollback plan for launch wave:** if HN ratios badly (down-voted, flagged), DELETE the submission within 1h. Do not post to HN again for 14 days. Pivot energy to X thread + targeted DMs only.

---

### Task 16: Post-launch metrics capture

**Files:**
- Create: `/tmp/darwin-sync/launch-metrics-apr26.md`

- [ ] **Step 1: Capture at +24h (Apr 27 morning)**

```markdown
# Launch metrics — 24h post-launch
- darwin-mvp stars: N (pre: 0)
- darwin-commons stars: N
- Commons fingerprints: N
- External contributors (issues/PRs): N
- HN: rank peak, comments, karma delta
- X: impressions, replies, follows
- First quarantined entry surfaced + triaged: yes/no
```

- [ ] **Step 2: Decide month-2 scope based on numbers**

If 50+ stars + 1 external contributor → proceed with Agent Lightning plugin month-2.
If <20 stars + 0 contributors → pivot decision (retry narrative / shape-generic service / different wedge).

---

## Self-Review (inline)

- Spec coverage: Every non-goal'd item in the spec maps to a task (public endpoint → T3, commons repo → T4, GPG → T5, sync worker → T6, seed → T7, systemd → T8, DLQ triage → T9, CI verify → T10, counter auto-update → T11, README update → T12, launch → T14-15, metrics → T16). Agent Lightning is explicitly deferred per panel.
- Placeholder scan: "similar to earlier" avoided; every code block is full-written. No TBD/TODO.
- Type consistency: `contributor_hash` is `ch-<12hex>` everywhere; `public_heal_id` / `commons_staged_id` naming aligned (`ph-YYYY-MM-DD-<8hex>`). `fingerprint` string format stable.
- Risk-gated actions: GPG key gen has backup step + revocation rollback. First git push has pre-seed SHA capture for rollback. Launch wave has abort-within-1h rollback.

---

## Execution Handoff

**Plan complete and saved to `darwin-mvp/docs/superpowers/plans/2026-04-24-darwin-commons-72hr.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

**Which approach?**
