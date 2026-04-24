#!/usr/bin/env python3
"""evo — Darwin's evolutionary engine (DGM-style).

Pieces:
  Archive       versioned store of agent variants (dir of JSON + append-only log)
  Variant       one point in the population (config + score + lineage)
  evaluate()    run variant against fixed benchmark subset, return score
  mutate()      reflective mutator — reads a failing trace, proposes child config
  gate()        AST + held-out eval — reject regressions before archiving
  sample()      score × 1/(child_count+1) weighted parent pick

No magic. Designed to be re-run from CLI one step at a time until the loop feels safe.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
ARCHIVE_DIR = Path(os.environ.get("DARWIN_ARCHIVE_DIR", ROOT / "archive"))
VARIANTS_DIR = ARCHIVE_DIR / "variants"
MANIFEST = ARCHIVE_DIR / "manifest.jsonl"
HELD_OUT_CORPORA = ["v3"]  # fast (10 strict real bugs) — gate subset
FULL_CORPORA = ["v3", "v1"]  # v3 + v1 for scoring; v2 is 171 bugs, skip by default
GATE_CAP = int(os.environ.get("DARWIN_GATE_CAP", "5"))  # bugs per corpus at gate
ACCEPT_EVAL_CAP = int(os.environ.get("DARWIN_ACCEPT_CAP", "20"))  # bugs per corpus post-accept


# ---------- Variant ----------
@dataclass
class Variant:
    id: str
    parent_id: str | None
    generation: int
    config: dict[str, Any]
    score: float | None = None
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    children: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def new(cls, config: dict[str, Any], parent: "Variant | None" = None, notes: str = "") -> "Variant":
        return cls(
            id=f"v{uuid.uuid4().hex[:8]}",
            parent_id=parent.id if parent else None,
            generation=(parent.generation + 1) if parent else 0,
            config=config,
            score=None,
            score_breakdown={},
            created_at=datetime.now(timezone.utc).isoformat(),
            children=[],
            notes=notes,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> "Variant":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


# ---------- Archive ----------
class Archive:
    def __init__(self, root: Path = ARCHIVE_DIR) -> None:
        self.root = root
        self.variants_dir = root / "variants"
        self.manifest = root / "manifest.jsonl"

    def ensure(self) -> None:
        self.variants_dir.mkdir(parents=True, exist_ok=True)
        self.manifest.touch()

    def _path(self, vid: str) -> Path:
        return self.variants_dir / f"{vid}.json"

    def add(self, v: Variant) -> Variant:
        self.ensure()
        self._path(v.id).write_text(v.to_json())
        with self.manifest.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "id": v.id,
                        "parent_id": v.parent_id,
                        "generation": v.generation,
                        "score": v.score,
                        "at": v.created_at,
                    }
                )
                + "\n"
            )
        if v.parent_id:
            try:
                parent = self.get(v.parent_id)
                if v.id not in parent.children:
                    parent.children.append(v.id)
                    self._path(parent.id).write_text(parent.to_json())
            except Exception:
                pass
        return v

    def update(self, v: Variant) -> Variant:
        self._path(v.id).write_text(v.to_json())
        return v

    def get(self, vid: str) -> Variant:
        data = json.loads(self._path(vid).read_text())
        return Variant.from_dict(data)

    def list(self) -> list[Variant]:
        self.ensure()
        out = []
        for p in sorted(self.variants_dir.glob("v*.json")):
            try:
                out.append(Variant.from_dict(json.loads(p.read_text())))
            except Exception:
                continue
        return out


# ---------- Baseline seed config ----------
def seed_config() -> dict[str, Any]:
    return {
        "corpora": FULL_CORPORA,
        "llm_provider": "gemini-flash",
        "no_cache": True,
        "timeout_per_bug": 60,
        "ast_gate": True,
        "heuristic_rules": [],
        "prompt_overrides": {},
        "retry_policy": {"max": 3, "backoff": 1.5},
    }


# ---------- Evaluator ----------
def _eval_one_bug(source_code: str, stderr: str, no_cache: bool, timeout_s: int) -> dict:
    """Run harness on one bug; return {healed, reasons, elapsed, provider}."""
    sys.path.insert(0, str(ROOT))
    from darwin_harness import diagnose_and_fix, validate_fix  # type: ignore
    from blackboard import lookup  # type: ignore

    t0 = time.time()
    try:
        # no-cache: skip cache lookup
        cached = None if no_cache else lookup(stderr)
        if cached:
            fix_src = cached.get("fix_code") or cached.get("code")
            provider = "cache"
        else:
            fix_src = diagnose_and_fix(source_code, stderr)
            provider = "llm"
        if not fix_src:
            return {"healed": False, "reasons": ["no fix produced"], "elapsed_s": round(time.time() - t0, 2), "provider": provider}
        ok, reasons = validate_fix(source_code, fix_src, stderr)
        return {
            "healed": bool(ok),
            "reasons": reasons or [],
            "elapsed_s": round(time.time() - t0, 2),
            "provider": provider,
        }
    except Exception as e:
        return {"healed": False, "reasons": [f"exception: {type(e).__name__}: {e}"], "elapsed_s": round(time.time() - t0, 2), "provider": "error"}


def evaluate(variant: Variant, corpora: list[str] | None = None, max_bugs_per_corpus: int | None = None) -> tuple[float, dict]:
    """Run variant in-process against real-bug corpora under darwin-mvp/benchmarks/<corpus>/bug_*.json.

    Score = total_healed / total_attempted across all corpora.
    Each bug scored independently; --no-cache drives mean differences between variants.
    """
    corps = corpora or variant.config.get("corpora", FULL_CORPORA)
    no_cache = bool(variant.config.get("no_cache", True))
    timeout_s = int(variant.config.get("timeout_per_bug", 60))
    breakdown: dict[str, Any] = {"corpora": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    t_all = time.time()
    total_healed, total_attempted, total_skipped = 0, 0, 0

    # Point harness at a variant-specific fixes dir so variants don't share cache state.
    sys.path.insert(0, str(ROOT))
    try:
        from blackboard import set_fixes_dir  # type: ignore
        fixes_root = ROOT / "fixes-evo" / variant.id
        fixes_root.mkdir(parents=True, exist_ok=True)
        set_fixes_dir(fixes_root)
    except Exception:
        pass

    for name in corps:
        bug_dir = ROOT / "benchmarks" / name
        bugs = sorted(bug_dir.glob("bug_*.json"))
        if max_bugs_per_corpus:
            bugs = bugs[:max_bugs_per_corpus]
        c_healed, c_attempted, c_skipped = 0, 0, 0
        per_bug: list[dict] = []
        for b in bugs:
            try:
                entry = json.loads(b.read_text())
            except Exception:
                c_skipped += 1
                continue
            src = entry.get("source_code") or entry.get("src")
            err = entry.get("stderr") or entry.get("err")
            if not (src and err):
                c_skipped += 1
                per_bug.append({"id": b.stem, "status": "skipped_no_repro"})
                continue
            res = _eval_one_bug(src, err, no_cache=no_cache, timeout_s=timeout_s)
            per_bug.append({"id": b.stem, **res})
            c_attempted += 1
            if res.get("healed"):
                c_healed += 1
        pr = c_healed / c_attempted if c_attempted else 0.0
        breakdown["corpora"][name] = {
            "healed": c_healed,
            "attempted": c_attempted,
            "skipped": c_skipped,
            "pass_rate": round(pr, 3),
            "bugs": per_bug[:30],  # cap for storage
        }
        total_healed += c_healed
        total_attempted += c_attempted
        total_skipped += c_skipped

    score = (total_healed / total_attempted) if total_attempted else 0.0
    breakdown["duration_s"] = round(time.time() - t_all, 2)
    breakdown["total_healed"] = total_healed
    breakdown["total_attempted"] = total_attempted
    breakdown["total_skipped"] = total_skipped
    breakdown["mean_pass_rate"] = round(score, 3)
    return score, breakdown


# ---------- Mutator ----------
MUTATION_PROMPT = """You are mutating an AI agent's reliability config to improve its ability to self-heal runtime failures.

Current config (JSON):
{config}

Evaluation breakdown (which scenarios failed):
{breakdown}

Propose ONE focused mutation to the config to address the worst failing scenario. Valid mutations:
  - add a heuristic rule (regex → patch template) to heuristic_rules
  - add or tweak a prompt override in prompt_overrides
  - adjust retry_policy
  - change max_llm_calls_per_scenario

Return strict JSON only:
{{
  "mutation_type": "heuristic_rule|prompt_override|retry_policy|llm_calls",
  "change": {{...patch payload...}},
  "rationale": "one-sentence why"
}}
No markdown, no prose, JSON only.
"""


def _call_gemini(prompt: str) -> str | None:
    try:
        from google import genai  # type: ignore
    except Exception:
        return None
    try:
        client = genai.Client()
        model = os.environ.get("DARWIN_GEMINI_MODEL", "gemini-2.5-flash")
        resp = client.models.generate_content(model=model, contents=prompt)
        if resp.candidates:
            return resp.candidates[0].content.parts[0].text
        return getattr(resp, "text", None)
    except Exception:
        return None


def _call_opus_cli(prompt: str) -> str | None:
    """Opus 4.7 via `claude -p` — uses user's Max subscription, strict JSON output."""
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", "opus", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _call_alibaba(prompt: str) -> str | None:
    """Alibaba Qwen Coder via DashScope OpenAI-compatible endpoint. Needs ALIBABA_CODING_API_KEY."""
    key = os.environ.get("ALIBABA_CODING_API_KEY")
    if not key:
        return None
    try:
        import urllib.request, urllib.error
        body = json.dumps({
            "model": os.environ.get("DARWIN_ALIBABA_MODEL", "qwen-coder-plus"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _call_mutator_llm(prompt: str) -> str | None:
    """Cascade: configured primary → alibaba → gemini → opus CLI. All failures = None."""
    provider = os.environ.get("DARWIN_MUTATOR_PROVIDER", "alibaba").lower()
    order = {
        "alibaba": [_call_alibaba, _call_gemini, _call_opus_cli],
        "gemini": [_call_gemini, _call_alibaba, _call_opus_cli],
        "opus": [_call_opus_cli, _call_alibaba, _call_gemini],
    }.get(provider, [_call_alibaba, _call_gemini, _call_opus_cli])
    for fn in order:
        out = fn(prompt)
        if out and "{" in out and "}" in out:
            return out
    return None


def mutate(parent: Variant) -> Variant | None:
    """Reflective mutator — reads parent breakdown, proposes child config."""
    if not parent.score_breakdown:
        return None
    prompt = MUTATION_PROMPT.format(
        config=json.dumps(parent.config, indent=2),
        breakdown=json.dumps(parent.score_breakdown, indent=2)[:2000],
    )
    raw = _call_mutator_llm(prompt)
    if not raw:
        return None
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        proposal = json.loads(raw[start:end])
    except Exception:
        return None

    child_config = json.loads(json.dumps(parent.config))
    mt = proposal.get("mutation_type", "")
    ch = proposal.get("change", {})
    # unwrap common double-nesting: {"retry_policy": {...}} → {...}
    if isinstance(ch, dict) and mt in ch and isinstance(ch[mt], dict):
        ch = ch[mt]
    if mt == "heuristic_rule" and ch:
        child_config.setdefault("heuristic_rules", []).append(ch)
    elif mt == "prompt_override" and ch:
        child_config.setdefault("prompt_overrides", {}).update(ch if isinstance(ch, dict) else {})
    elif mt == "retry_policy" and isinstance(ch, dict):
        merged = {**child_config.get("retry_policy", {})}
        for k, v in ch.items():
            if k == "retry_policy" and isinstance(v, dict):
                merged.update(v)
            else:
                merged[k] = v
        child_config["retry_policy"] = merged
    elif mt == "llm_calls" and isinstance(ch, dict):
        if "max" in ch:
            child_config["max_llm_calls_per_scenario"] = int(ch["max"])
    else:
        return None

    return Variant.new(
        config=child_config,
        parent=parent,
        notes=f"mutation_type={mt} rationale={proposal.get('rationale','')[:140]}",
    )


# ---------- Gate ----------
def gate(child: Variant, baseline: Variant, min_delta: float = 0.0) -> tuple[bool, str]:
    """Merge gate — child passes if score_held_out >= baseline.score + min_delta.

    Runs held-out subset only (fast). AST gate is already enforced inside benchmark.py.
    """
    score, breakdown = evaluate(child, corpora=HELD_OUT_CORPORA, max_bugs_per_corpus=GATE_CAP)
    child.score_breakdown["gate"] = breakdown
    if baseline.score is None:
        return (score > 0.0, f"baseline unscored; child held-out={score:.2f}")
    ok = score >= (baseline.score + min_delta)
    return (ok, f"child_held_out={score:.2f} vs baseline={baseline.score:.2f} delta≥{min_delta}")


# ---------- Sampler ----------
def sample_parent(archive: Archive, rng: random.Random | None = None) -> Variant | None:
    """Score × 1/(children+1) weighted pick."""
    rng = rng or random.Random()
    variants = [v for v in archive.list() if v.score is not None]
    if not variants:
        return None
    weights = []
    for v in variants:
        s = max(v.score or 0.0, 0.01)
        w = s / (1 + len(v.children))
        weights.append(w)
    total = sum(weights) or 1.0
    r = rng.random() * total
    acc = 0.0
    for v, w in zip(variants, weights):
        acc += w
        if r <= acc:
            return v
    return variants[-1]


# ---------- One-shot loop step ----------
def step(archive: Archive) -> dict[str, Any]:
    """Sample → mutate → gate → archive. Returns a report dict."""
    parent = sample_parent(archive)
    if parent is None:
        return {"status": "no_parent", "reason": "archive has no scored variants"}
    child = mutate(parent)
    if child is None:
        return {"status": "mutation_failed", "parent": parent.id}
    ok, reason = gate(child, parent)
    if not ok:
        return {"status": "rejected", "parent": parent.id, "child": child.id, "reason": reason}
    score, breakdown = evaluate(child, corpora=child.config.get("corpora", FULL_CORPORA), max_bugs_per_corpus=ACCEPT_EVAL_CAP)
    child.score = score
    child.score_breakdown.update(breakdown)
    archive.add(child)
    return {
        "status": "accepted",
        "parent": parent.id,
        "child": child.id,
        "parent_score": parent.score,
        "child_score": score,
        "gate": reason,
    }
