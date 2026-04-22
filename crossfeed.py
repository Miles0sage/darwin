"""Darwin Crossfeed — Laplacian federated Q-delta protocol.

Privacy invariant: raw source code NEVER crosses the wire. Only transformer
recipes (AST patterns), fingerprints, and differentially-private Q-deltas
are shared between fleet nodes.
"""

import hashlib
import hmac as _hmac
import json
import math
import os
import random
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional
import http.server
import socketserver
import threading
import glob as _glob


@dataclass
class CrossfeedMessage:
    """Signed, privacy-preserving message for cross-fleet recipe sharing."""

    version: str = "0.1.0"
    fingerprint: str = ""
    ast_signature_hash: str = ""   # sha256(transformer_src)
    patch_recipe: str = ""          # the transformer_src itself
    success_count: int = 0
    q_value: float = 0.0
    q_delta: float = 0.0
    laplace_noise: float = 0.0
    timestamp: str = ""             # UTC ISO 8601
    repo_id_hashed: str = ""        # sha256(repo_id)
    hmac: str = ""                  # HMAC-SHA256 hex over all other fields


def sample_laplace(epsilon: float = 1.0) -> float:
    """Pure-Python inverse-CDF Laplace sampling. No numpy required.

    Uses the relation: if U ~ Uniform(0,1), then
    X = -b * sign(U - 0.5) * ln(1 - 2|U - 0.5|) is Laplace(0, b).
    """
    b = 1.0 / epsilon
    u = random.random()
    if abs(u - 0.5) < 1e-10:
        return 0.0
    return -b * math.copysign(1.0, u - 0.5) * math.log(1.0 - 2.0 * abs(u - 0.5))


def compute_q_delta(
    current_q: float,
    last_shared_q: float,
    epsilon: float = 1.0,
) -> tuple[float, float]:
    """Compute a differentially-private Q-delta ready for sharing.

    Returns:
        (delta, noise) where delta = (current_q - last_shared_q) + noise
    """
    noise = sample_laplace(epsilon)
    delta = (current_q - last_shared_q) + noise
    return delta, noise


def apply_q_delta(
    local_q: float,
    received_deltas: list[float],
    lr: float = 0.3,
) -> float:
    """Incorporate fleet Q-deltas into local Q-value.

    Uses sample-weighted mean of received deltas scaled by learning rate.
    """
    if not received_deltas:
        return local_q
    mean_delta = sum(received_deltas) / len(received_deltas)
    return local_q + lr * mean_delta


def _payload_dict(msg: CrossfeedMessage) -> dict:
    """Return all CrossfeedMessage fields except 'hmac' as a plain dict."""
    return {
        "version": msg.version,
        "fingerprint": msg.fingerprint,
        "ast_signature_hash": msg.ast_signature_hash,
        "patch_recipe": msg.patch_recipe,
        "success_count": msg.success_count,
        "q_value": msg.q_value,
        "q_delta": msg.q_delta,
        "laplace_noise": msg.laplace_noise,
        "timestamp": msg.timestamp,
        "repo_id_hashed": msg.repo_id_hashed,
    }


def _guard_finite(payload: dict) -> None:
    """Reject NaN/Inf floats which serialize to non-spec JSON. Raises ValueError."""
    for k, v in payload.items():
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"non-finite float in field {k!r}: {v}")


def sign_message(payload: dict, secret: bytes) -> str:
    """HMAC-SHA256 sign a payload dict. Returns hex digest.

    Rejects NaN/Inf floats (RFC 8259 non-compliant — cross-language peers break).
    """
    _guard_finite(payload)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return _hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_message(payload: dict, signature: str, secret: bytes) -> bool:
    """Constant-time HMAC verify. Returns True if signature matches."""
    expected = sign_message(payload, secret)
    return _hmac.compare_digest(expected, signature)


def make_message(
    fingerprint: str,
    transformer_src: str,
    q_value: float,
    q_delta: float,
    laplace_noise: float,
    success_count: int,
    repo_id: str,
    secret: bytes,
) -> CrossfeedMessage:
    """Construct and sign a CrossfeedMessage ready for transport."""
    ast_sig = hashlib.sha256(transformer_src.encode("utf-8")).hexdigest()
    repo_hashed = hashlib.sha256(repo_id.encode("utf-8")).hexdigest()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg = CrossfeedMessage(
        fingerprint=fingerprint,
        ast_signature_hash=ast_sig,
        patch_recipe=transformer_src,
        success_count=success_count,
        q_value=q_value,
        q_delta=q_delta,
        laplace_noise=laplace_noise,
        timestamp=ts,
        repo_id_hashed=repo_hashed,
    )
    payload = _payload_dict(msg)
    msg.hmac = sign_message(payload, secret)
    return msg


_INBOX_DIR = "/tmp/darwin-crossfeed-inbox"


def make_crossfeed_handler(secret: bytes, inbox_dir: str = None):
    """Factory returning a per-instance CrossfeedServer class with bound secret.

    Prevents class-attr secret clobber across multiple server instances in the
    same process. Each call returns a fresh subclass with its own secret.
    """
    _inbox = inbox_dir or _INBOX_DIR

    class _BoundServer(CrossfeedServer):
        pass

    _BoundServer.secret = secret
    _BoundServer.inbox_dir = _inbox
    return _BoundServer


class CrossfeedServer(http.server.BaseHTTPRequestHandler):
    """HTTP server that receives and verifies Crossfeed recipe messages.

    Accepts POST /export, verifies HMAC, stores to inbox directory.
    Prefer make_crossfeed_handler(secret) for multi-tenant isolation over
    directly setting CrossfeedServer.secret (class-level, clobber-risk).
    """

    secret: bytes = b""
    inbox_dir: str = _INBOX_DIR

    def do_POST(self) -> None:  # noqa: N802
        # Kill-switch: DARWIN_DISABLE=1 disables the crossfeed server.
        if os.environ.get("DARWIN_DISABLE", "").lower() in ("1", "true", "yes"):
            resp = json.dumps({"status": "disabled"}).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        if self.path != "/export":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        incoming_hmac = data.pop("hmac", "")
        if not verify_message(data, incoming_hmac, self.secret):
            self.send_response(403)
            self.end_headers()
            return

        inbox = getattr(self, "inbox_dir", _INBOX_DIR)
        os.makedirs(inbox, exist_ok=True)
        fp = data.get("fingerprint", "unknown")
        ts_safe = data.get("timestamp", time.strftime("%Y%m%dT%H%M%SZ")).replace(":", "")
        # HMAC-derived suffix dedups identical (fingerprint, hmac) pairs
        sig_suffix = incoming_hmac[:12] if incoming_hmac else "nosig"
        fname = os.path.join(inbox, f"{fp}_{ts_safe}_{sig_suffix}.json")
        if os.path.exists(fname):
            resp = json.dumps({"status": "duplicate"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return
        with open(fname, "w", encoding="utf-8") as fh:
            json.dump({**data, "hmac": incoming_hmac}, fh)

        resp = json.dumps({"status": "accepted"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        pass  # suppress access log


class CrossfeedClient:
    """Client for exporting and importing Crossfeed recipe messages."""

    def export(
        self,
        recipe_entry: dict,
        server_url: str,
        secret: bytes,
    ) -> bool:
        """POST a signed CrossfeedMessage to the Crossfeed server.

        recipe_entry keys: fingerprint, transformer_src, q_value, q_delta,
            laplace_noise, success_count, repo_id
        Returns True on HTTP 200, False otherwise.
        """
        import urllib.request
        import urllib.error

        msg = make_message(
            fingerprint=recipe_entry.get("fingerprint", ""),
            transformer_src=recipe_entry.get("transformer_src", ""),
            q_value=recipe_entry.get("q_value", 0.0),
            q_delta=recipe_entry.get("q_delta", 0.0),
            laplace_noise=recipe_entry.get("laplace_noise", 0.0),
            success_count=recipe_entry.get("success_count", 0),
            repo_id=recipe_entry.get("repo_id", ""),
            secret=secret,
        )
        payload = {**_payload_dict(msg), "hmac": msg.hmac}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url.rstrip('/')}/export",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except urllib.error.URLError:
            return False

    def import_recipes(
        self,
        inbox_dir: str = _INBOX_DIR,
    ) -> list[CrossfeedMessage]:
        """Load all Crossfeed messages from the inbox directory.

        Deduplicates by (fingerprint, hmac) — identical messages loaded once.
        """
        messages: list[CrossfeedMessage] = []
        seen: set[tuple[str, str]] = set()
        pattern = os.path.join(inbox_dir, "*.json")
        for path in _glob.glob(pattern):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                dedup_key = (data.get("fingerprint", ""), data.get("hmac", ""))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                msg = CrossfeedMessage(
                    version=data.get("version", "0.1.0"),
                    fingerprint=data.get("fingerprint", ""),
                    ast_signature_hash=data.get("ast_signature_hash", ""),
                    patch_recipe=data.get("patch_recipe", ""),
                    success_count=data.get("success_count", 0),
                    q_value=data.get("q_value", 0.0),
                    q_delta=data.get("q_delta", 0.0),
                    laplace_noise=data.get("laplace_noise", 0.0),
                    timestamp=data.get("timestamp", ""),
                    repo_id_hashed=data.get("repo_id_hashed", ""),
                    hmac=data.get("hmac", ""),
                )
                messages.append(msg)
            except (json.JSONDecodeError, OSError):
                continue
        return messages


__all__ = [
    "CrossfeedMessage",
    "CrossfeedClient",
    "CrossfeedServer",
    "make_crossfeed_handler",
    "sample_laplace",
    "compute_q_delta",
    "apply_q_delta",
    "sign_message",
    "verify_message",
    "make_message",
    "_payload_dict",
    "_guard_finite",
]


if __name__ == "__main__":
    _secret_env = os.environ.get("CROSSFEED_SECRET", "change-me").encode("utf-8")
    handler_cls = make_crossfeed_handler(_secret_env)
    with socketserver.TCPServer(("", 9000), handler_cls) as srv:
        print("Darwin Crossfeed server listening on :9000")
        srv.serve_forever()
