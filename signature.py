#!/usr/bin/env python3
"""
Darwin signature fingerprinting.

Goal: normalize a Python traceback/stderr into a stable 64-char hex fingerprint
that matches across:
  - Different worker tmpdirs (/tmp/darwin-worker-abc vs /tmp/darwin-worker-xyz)
  - Different source-file absolute paths (/home/a/main.py vs /srv/b/src/main.py)
  - Different line numbers within the same function
  - Different memory addresses in repr()
  - Different PIDs, UUIDs, timestamps embedded in error messages

What IS retained:
  - Error class (FileNotFoundError, KeyError, etc.)
  - Missing key / argument names
  - Basename of referenced files
  - Function names in traceback
  - The terminal error message structure (with variable parts masked)

Returns (fingerprint_hex_16, normalized_signature_string). The normalized
string is human-readable; the hex is for content-addressing in the
blackboard.
"""

from __future__ import annotations

import hashlib
import re

# Fast, ordered substitution table. Applied in sequence.
_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    # Strip Darwin per-worker tmpdirs
    (re.compile(r"/tmp/darwin-[^/\s]+/"), ""),
    # Strip any quoted path (absolute OR relative with >=1 slash), keep basename only.
    # matches: "/path/to/file.py", 'api/v3/data.json', etc.
    (re.compile(r'''(["\'])\s*(?:[^\s"\'<>]*/)+([^/\s"\'<>]+)(["\'])'''), r"\1\2\3"),
    # Line numbers in traceback: `line 43` → `line N`
    (re.compile(r"\bline\s+\d+\b"), "line N"),
    # Hex memory addresses: `0x7fabc123` → `0xMEM`
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "0xMEM"),
    # Python 3 frozen import paths
    (re.compile(r"<frozen [^>]+>"), "<frozen>"),
    # ISO timestamps
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"), "TIMESTAMP"),
    # UUIDs
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "UUID"),
    # PIDs in pytest/subprocess output: `pid=12345` or `[pid 12345]`
    (re.compile(r"\bpid[=\s]+\d+\b"), "pid=N"),
    # Standalone long integers (5+ digits) — addresses, ports, counts
    (re.compile(r"\b\d{5,}\b"), "N"),
    # Collapse runs of whitespace
    (re.compile(r"[ \t]+"), " "),
]

# Drop noisy banner lines inside tracebacks (the exact phrasing is stable enough not to need normalization).
_DROP_LINES = (
    "Traceback (most recent call last):",
)


def normalize(stderr: str) -> str:
    """Apply the substitution chain. Returns the normalized string."""
    text = stderr
    for pat, repl in _NORMALIZERS:
        text = pat.sub(repl, text)

    # Line-level cleanup: drop banners, keep structure.
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in _DROP_LINES:
            continue
        kept.append(stripped)
    return "\n".join(kept)


def error_class(stderr: str) -> str | None:
    """Extract the canonical error class name (e.g. 'KeyError')."""
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*Error)\b", stderr)
    return m.group(1) if m else None


_FILE_LINE_RE = re.compile(r'^File\s+"[^"]*",\s+line\s+\S+,\s+in\s+\S+')
_TRACEBACK_LINE_RE = re.compile(r'^File\s+"([^"]*)",\s+line\s+\S+,\s+in\s+(\S+)$')


# Identifier mask — replaces variable / function names with `_` so the
# cross-codebase core collapses `value = row["text"]` and `body = doc["text"]`
# to the same form: `_ = _["text"]`. String literals survive unchanged.
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z_0-9]*\b")
# Python keywords we DO want to keep (for/in/if/import) so structure reads.
_KEEP_TOKENS = frozenset(
    "for in if else elif while try except finally with as import from return "
    "yield raise class def lambda True False None not and or is pass break continue".split()
)


def _mask_identifiers(code_line: str) -> str:
    """Replace bare identifiers with `_` while preserving keywords + literals.

    Operates on one source line. Strings, numbers, and Python keywords are
    untouched. Best-effort — not AST-accurate — sufficient to make
    `a = b["text"]` and `body = doc["text"]` hash the same.
    """
    # Protect string literals by masking them out first, then restoring.
    placeholders: list[str] = []

    def _stash(m):
        placeholders.append(m.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    protected = re.sub(r'"[^"]*"|\'[^\']*\'', _stash, code_line)

    def _mask(m):
        tok = m.group(0)
        return tok if tok in _KEEP_TOKENS else "_"

    masked = _IDENT_RE.sub(_mask, protected)
    # Restore string literals.
    def _restore(m):
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, masked)


def _fingerprint_core(normalized: str) -> str:
    """Extract the cross-codebase-stable core from a normalized traceback.

    Keeps (all with identifier masking to survive rename-refactor):
      - Terminal `ErrorClass: msg` line
      - Last code line of traceback, with identifiers masked to `_`
    Drops:
      - Filenames, line numbers (already normalized)
      - Function names (vary by codebase)
      - Intermediate framework frames
    """
    lines = normalized.splitlines()
    error_line = None
    last_code_line = None

    for i, line in enumerate(lines):
        m = _TRACEBACK_LINE_RE.match(line)
        if m:
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and not _FILE_LINE_RE.match(nxt):
                    last_code_line = nxt
            continue
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*Error:\s*.*)$", line)
        if m:
            error_line = m.group(1)

    parts: list[str] = []
    if error_line:
        parts.append(error_line)
    if last_code_line:
        parts.append(f"code: {_mask_identifiers(last_code_line)}")
    return "\n".join(parts) if parts else normalized


def fingerprint(stderr: str) -> tuple[str, str]:
    """Return (16-char hex fingerprint, normalized_signature).

    The fingerprint hashes the CROSS-CODEBASE CORE (error class + msg,
    last-frame code line, last-frame function name). Filenames and line
    numbers are deliberately dropped so the SAME bug in DIFFERENT repos
    collides to the same hash — enabling cross-repo fix transfer.

    The human-readable normalized string retains File lines for debug.
    """
    normalized = normalize(stderr)
    core = _fingerprint_core(normalized)
    digest = hashlib.sha256(core.encode("utf-8")).hexdigest()[:16]
    return digest, normalized


__all__ = ["fingerprint", "normalize", "error_class"]
