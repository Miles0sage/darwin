# Darwin Benchmarks

Real Python agent failures harvested from public GitHub issues + StackOverflow. Permissive license (Apache-2.0 / MIT / BSD / CC BY-SA). 261 bugs across three corpora.

| Corpus | N | Heal rate | Provider mix | Notes |
|---|---|---|---|---|
| v1 runnable | 18 (of 40) | 94% (17/18) | Gemini Flash | 14 cache hits + 3 LLM + 1 structural skip |
| v2 complex | 171 | ~77% | Gemini Flash/Pro + Opus | $1.09 total LLM spend; Flash rate-limited, Pro carried |
| v3 strict | 50 | 100% (50/50) | Gemini + Opus | 34 Gemini + 16 Opus rescues |

`results.json` in each dir contains per-bug outcomes (provider_used, healed, latency, notes).

## Reproducibility

```bash
pip install libcst google-genai anthropic
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...   # optional, used for Opus fallback
python benchmarks/run.py --corpus v3
```

Results will be written to `benchmarks/v3/results-$(date).json`.

## License

Each bug JSON includes a `license` field. Aggregate usage follows the fair-use norm for public GitHub issue tracebacks + StackOverflow CC BY-SA content.
