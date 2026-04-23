"""Smoke tests for commons_sync worker."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


@pytest.fixture
def fake_commons_repo(tmp_path, monkeypatch):
    """Ephemeral git repo with empty fingerprints/quarantine files."""
    repo = tmp_path / "commons"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "fingerprints.jsonl").touch()
    (repo / "quarantine.jsonl").touch()
    (repo / "transformers").mkdir()
    (repo / "transformers" / ".gitkeep").touch()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("DARWIN_COMMONS_REPO", str(repo))
    return repo


@pytest.fixture
def staging_dir(tmp_path, monkeypatch):
    d = tmp_path / "staging"
    d.mkdir()
    monkeypatch.setenv("DARWIN_STAGING_DIR", str(d))
    return d


def test_sync_noop_without_staging_file(staging_dir, fake_commons_repo):
    import commons_sync
    # Re-import module-level constants in case another test ran first
    import importlib
    importlib.reload(commons_sync)
    rc = commons_sync.main()
    assert rc == 0


def test_sync_idempotent_empty_run(staging_dir, fake_commons_repo):
    (staging_dir / "pending.jsonl").write_text("")
    import commons_sync, importlib
    importlib.reload(commons_sync)
    rc1 = commons_sync.main()
    rc2 = commons_sync.main()
    assert rc1 == 0 and rc2 == 0


def test_sync_quarantines_entry_with_missing_new_source(staging_dir, fake_commons_repo):
    entry = {
        "fingerprint": "qtest",
        "error_class": "Xerr",
        "stderr": "Traceback (most recent call last):\n  File \"a.py\"\nXerr: x",
        "source_code": "x=1\n",
        "new_source": "",  # empty triggers quarantine
    }
    (staging_dir / "pending.jsonl").write_text(json.dumps(entry) + "\n")
    import commons_sync, importlib
    importlib.reload(commons_sync)
    rc = commons_sync.main()
    assert rc == 0
    q = (fake_commons_repo / "quarantine.jsonl").read_text()
    assert "qtest" in q
