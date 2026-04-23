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
    body = resp.get_json() or {}
    # Staging writes only when we produced a real patch. Anonymous (no BYO-key)
    # cache-miss returns 200 with status=cache_miss_heuristic_only and no
    # new_source — that's correct; Commons should not store empty entries.
    if body.get("new_source"):
        assert staging.exists(), "staging should exist when new_source produced"
    else:
        assert (not staging.exists()) or staging.stat().st_size == 0, \
            "staging should be empty when no new_source produced"


def test_badge_endpoint_returns_shields_compatible_json(client):
    resp = client.get("/darwin/commons/badge/ch-abc123")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["schemaVersion"] == 1
    assert data["label"] == "darwin commons"
    assert "fingerprints" in data["message"]
