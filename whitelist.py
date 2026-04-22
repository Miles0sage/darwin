"""Darwin Signed-Template Whitelist — opt-in enforcement of approved recipes.

Empty whitelist = allow-all (enforcement disabled by default).
Set DARWIN_WHITELIST_ENFORCE=1 to enable strict enforcement.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


WHITELIST_PATH = os.environ.get(
    "DARWIN_WHITELIST_PATH", "/tmp/darwin-crossfeed-whitelist.json"
)


@dataclass
class WhitelistEntry:
    fingerprint: str
    ast_signature_hash: str
    approved_by: str
    approved_at: str  # UTC ISO 8601


class Whitelist:
    """Load/save/query the approved-recipe whitelist."""

    def __init__(self) -> None:
        self._entries: list[WhitelistEntry] = []

    def load(self, path: str = WHITELIST_PATH) -> "Whitelist":
        """Load entries from JSON file. Returns self for chaining."""
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._entries = [WhitelistEntry(**e) for e in raw]
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self._entries = []
        return self

    def save(self, path: str = WHITELIST_PATH) -> None:
        """Persist entries to JSON file."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([asdict(e) for e in self._entries], fh, indent=2)

    def add(self, entry: WhitelistEntry) -> None:
        """Add an entry (deduplicates by fingerprint + ast_signature_hash)."""
        key = (entry.fingerprint, entry.ast_signature_hash)
        existing_keys = {(e.fingerprint, e.ast_signature_hash) for e in self._entries}
        if key not in existing_keys:
            self._entries.append(entry)

    def is_approved(self, fingerprint: str, ast_signature_hash: str) -> bool:
        """Return True if (fingerprint, ast_signature_hash) is on the whitelist."""
        for entry in self._entries:
            if entry.fingerprint == fingerprint and entry.ast_signature_hash == ast_signature_hash:
                return True
        return False

    def __len__(self) -> int:
        return len(self._entries)


def enforcement_enabled() -> bool:
    """Return True when DARWIN_WHITELIST_ENFORCE=1."""
    return os.environ.get("DARWIN_WHITELIST_ENFORCE", "").strip() == "1"


__all__ = ["WhitelistEntry", "Whitelist", "enforcement_enabled", "WHITELIST_PATH"]
