#!/usr/bin/env bash
# Darwin Multi-LLM Variance Benchmark v0 — reproducibility script.
# Runs the LangGraph slice (42 bugs) across 5 providers and aggregates.
set -euo pipefail
cd "$(dirname "$0")/.."  # repo root

# 1. env vars — fail fast with where to get each.
miss=0
[[ -n "${GEMINI_API_KEY:-}" ]]                                || { echo "missing GEMINI_API_KEY (https://aistudio.google.com/apikey)"; miss=1; }
[[ -n "${ALIBABA_CODING_API_KEY:-${DASHSCOPE_API_KEY:-}}" ]]  || { echo "missing ALIBABA_CODING_API_KEY (https://dashscope-intl.console.aliyun.com)"; miss=1; }
[[ -n "${ZHIPU_API_KEY:-${GLM_API_KEY:-}}" ]]                 || { echo "missing ZHIPU_API_KEY (https://z.ai coding plan)"; miss=1; }
command -v claude >/dev/null                                  || { echo "missing 'claude' CLI on PATH (Anthropic Max sub)"; miss=1; }
[[ "$miss" -eq 0 ]] || exit 1

# 2. deps — requirements.txt is minimal; cryptography pulled in by genome.py.
[[ -f requirements.txt ]] && pip install -q -r requirements.txt
pip install -q "cryptography>=41"

# 3-4. matrix run + GLM pass (writes datasets/matrix/matrix.jsonl).
python3 datasets/matrix/run_matrix.py all
python3 datasets/matrix/glm_pass.py

# 5. aggregate + print headline.
python3 datasets/matrix/aggregate.py
echo "DONE — see datasets/matrix/summary.json"
