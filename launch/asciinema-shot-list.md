# Darwin Demo — Asciinema Shot List
**Target:** 180-220s | `asciinema rec darwin-demo.cast` | dark bg, ≥14pt font

---

## PRE-RECORD CHECKLIST (copy-paste in order)

```bash
rm -f /tmp/darwin-crossfeed-inbox/*.json
rm -f /tmp/darwin-budget.json
export PYTHONPATH=/root/claude-code-agentic/darwin-mvp:$PYTHONPATH
cd /root/claude-code-agentic/darwin-mvp
clear
asciinema rec darwin-demo.cast
```

---

## SCENE 1 — Hook (20s)

**Goal:** Tagline lands, then real failure scrolls. Viewer is hooked.

```bash
printf '\033[1;36m Darwin: AST-level structural patching for Python agents.\033[0m\n'
printf '\033[1;36m Bounded blast radius. Vendor-neutral. MIT.\033[0m\n'
sleep 1
python3 agent.py
```

**Expected output:** agent.py crashes with a KeyError or AttributeError from config.yaml (v2 path missing). Full traceback visible.

**Pacing:** Pause 2s after traceback. Let it breathe before typing next command.

---

## SCENE 2 — Triage (25s)

**Goal:** Show Darwin labels failures before touching them. Flakes get skipped.

```bash
python3 -c "
from triage import classify
src = open('agent.py').read()
r = classify(src, 'AttributeError: NoneType has no attribute run')
print(r)
"
sleep 0.5
python3 -c "
from triage import classify
r = classify('', 'TimeoutError: timed out after 30s')
print(r)
"
```

**Expected output (line 1):** `TriageResult(label='fixable', confidence=0.6, reason='default', features={...})`
**Expected output (line 2):** `TriageResult(label='flaky', confidence=0.85, reason='TimeoutError match', features={...})`

**Pacing:** After second result, say aloud: "Darwin doesn't touch flakes." Pause 1s.

---

## SCENE 3 — Heal (40s)

**Goal:** Live Gemini call fixes the broken agent. Real LLM, real output.

```bash
export GEMINI_API_KEY=$(grep GEMINI_API_KEY /root/ai-factory/.env | cut -d= -f2-)
python3 -c "
from darwin_harness import diagnose_and_fix
src = open('agent.py').read()
err = 'AttributeError: NoneType has no attribute run'
fix = diagnose_and_fix(src, err)
print(fix[:500] if fix else 'no fix returned')
"
```

**Expected output:** ~10-15s pause (Gemini call), then Python source diff/fix printed. First 500 chars show the patched function.

**Backup (if Gemini 503s):** Pre-stage a cached fix in blackboard. Run instead:
```bash
python3 -c "
import blackboard, json
hits = blackboard.lookup('AttributeError: NoneType')
print(json.dumps(hits[0], indent=2) if hits else 'no cache hit')
"
```
This returns in <0.01s from blackboard — label it "cache hit, 0 LLM calls."

**Pacing:** Let Gemini call run visibly. Don't type during it.

---

## SCENE 4 — AST Gate (30s)

**Goal:** Prove Darwin rejects poisoned patches before they ship.

```bash
python3 -c "
from darwin_harness import validate_fix
old = 'def f():\n    return x.run()'
good = 'def f():\n    return (x or default).run()'
bad  = 'def f():\n    try:\n        return x.run()\n    except Exception:\n        pass'
ok, _ = validate_fix(old, good, 'AttributeError')
print('good patch:', ok)
ok2, reasons = validate_fix(old, bad, 'AttributeError')
print('bad patch:', ok2, '|', reasons)
"
```

**Expected output:**
```
good patch: True
bad patch: False | ['new \`except Exception:\` broadened error handling']
```

**Pacing:** Read the rejection reason aloud. "Broad except — swallows all errors. Darwin blocks it."

---

## SCENE 5 — Crossfeed (40s)

**Goal:** Repo-A fix propagates to Repo-B with zero LLM calls. The moat.

```bash
python3 demo_crossfeed.py
```

**Expected output:** Three-scene colored banner sequence:
- Scene 1: `[ERROR] AttributeError` → heal → recipe exported
- Scene 2: HMAC verified, Q-delta noise shown
- Scene 3: Repo B autopatches — "0 LLM calls used"

**Pacing:** Let it run fully without interruption. It's the most visual scene.
If import error: `export PYTHONPATH=$(pwd):$PYTHONPATH` then re-run.

---

## SCENE 6 — Benchmark Receipt (30s)

**Goal:** Numbers on screen. This is the close.

```bash
python3 -c "
import json
d = json.load(open('benchmark-report.json'))
print(f'Scenarios:      {d[\"scenarios\"]}')
print(f'Fleet size:     {d[\"total_agents\"]} agents')
print(f'Healed:         {d[\"healed\"]}/{d[\"total_agents\"]}')
print(f'LLM calls:      {d[\"llm_calls\"]}')
print(f'Blackboard hits:{d[\"blackboard_hits\"]}')
print(f'Wall clock:     {d[\"grand_wall_clock_ms\"]}ms')
"
sleep 0.5
echo "github.com/Miles0sage/darwin — MIT — vendor-neutral — 0 lock-in"
```

**Expected output:**
```
Scenarios:      ['timeout']
Fleet size:     3 agents
Healed:         3/3
LLM calls:      1
Blackboard hits:2
Wall clock:     25538ms
github.com/Miles0sage/darwin — MIT — vendor-neutral — 0 lock-in
```

**Pacing:** After URL prints, hold 3s, then Ctrl-D.

---

## POST-RECORD CHECKLIST

```bash
# Review
asciinema play darwin-demo.cast

# Upload (creates shareable URL)
asciinema upload darwin-demo.cast

# Paste URL into:
# - README.md  ([![asciicast](badge-url)](cast-url))
# - LinkedIn profile Featured section
# - X/Twitter bio link
# - Hackathon submission page
```

---

## GOTCHAS + BACKUP PLANS

| Risk | Mitigation |
|------|-----------|
| Gemini 503 throttle | Use blackboard cache path in Scene 3 (shown above) |
| `demo_crossfeed.py` ImportError | `export PYTHONPATH=$(pwd):$PYTHONPATH` before re-run |
| `asciinema` not installed | `pip install asciinema` or `apt install asciinema -y` |
| `agent.py` doesn't crash cleanly | Add `python3 -c "import agent; agent.main()"` as fallback |
| Prompt is distracting | `export PS1='$ '` before recording |
| Terminal too narrow | `resize` to ≥120 cols before `rec` |

---

## CANDIDATE TAGLINES (thumbnail / LinkedIn caption)

1. **"1 LLM call healed 3 agents. The other 2 were free."**
2. **"Your agents will fail. Darwin decides: fix, skip, or escalate — automatically."**

---

## TIMING BUDGET

| Scene | Target | Cumulative |
|-------|--------|-----------|
| 1 Hook | 20s | 0:20 |
| 2 Triage | 25s | 0:45 |
| 3 Heal | 40s | 1:25 |
| 4 AST Gate | 30s | 1:55 |
| 5 Crossfeed | 40s | 2:35 |
| 6 Benchmark | 30s | 3:05 |
| **Total** | **185s** | **~3:05** |
