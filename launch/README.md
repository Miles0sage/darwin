# Darwin Launch Assets — Index + Posting Order

**Honest tone, not marketing.** Every claim cites a file on disk. No fabricated "we deployed 3000 agents" stories.

## Assets in this directory

| File | What | Length |
|---|---|---|
| `linkedin-post.md` | LinkedIn feed post | ~600 chars |
| `linkedin-headline.md` | Profile headline | ~215 chars |
| `x-thread.md` | X thread, 5 tweets | ~1,400 chars |
| `devto-post.md` | Long-form blog | ~1,200 words |
| `signature-email.md` | Cold-email sig block | ~60 words |
| `asciinema-shot-list.md` | 6-scene 3-min demo script | ~800 words |
| `hn-launch-comment.md` | Updated HN first-comment | (in hn-launch-thread.md) |

## Posting order (5-day sprint)

### Day 1 — today
- [ ] Push `Miles0sage/darwin` public (me)
- [ ] Update LinkedIn headline — copy from `linkedin-headline.md`
- [ ] Write new LinkedIn post — copy `linkedin-post.md`

### Day 2 — tomorrow
- [ ] Record asciinema demo using `asciinema-shot-list.md` (5-10 min)
- [ ] Upload to asciinema.org, paste URL into repo README + LinkedIn + email signature
- [ ] Post X thread (tweets 1-5, 15-30 min spacing)

### Day 3
- [ ] Publish dev.to / Substack post from `devto-post.md`
- [ ] Cross-link LinkedIn + X to the blog post
- [ ] Reply to every comment within 2 hours

### Day 4
- [ ] Show HN submission Tuesday-Thursday 8-11am Pacific
- [ ] Title: "[Show HN] Durable patch execution for Python agents — 12/12 Opus, 2/12 Gemini"
- [ ] First comment: final numbers + caveats (see hn-launch-thread.md)

### Day 5
- [ ] Monitor inbound (LinkedIn DMs, repo issues, HN comments, Twitter)
- [ ] Verify 3 Mittelstand prospects on LinkedIn (`/tmp/darwin-sync/prospects-10.csv`)
- [ ] Send first cold email — Template B (English) or E (Deutsch) — with asciinema + repo URL in signature

## Rules

1. No claim we cannot back with a path like `/tmp/darwin-sync/real-bugs-v3-results.json`
2. No "we deploy 3000 agents". Solo builder, beta, 0 paid users.
3. "Durable patch execution with bounded blast radius" — not "self-heal"
4. Lead with Opus 12/12 vs Gemini 2/12 or with 100% on 50 strict bugs
5. NeoCognition's ~50% stat is the market hook
6. Disclose caveats up front (small N, no baseline delta yet, no crossfeed between real machines yet)

## What NOT to say

- "Revolutionary"
- "Self-healing"
- "Agents magically fix themselves"
- "Outperforms GPT-4"
- "Production-ready" (it is beta)
- Any number not backed by a JSON on disk
- "We" when it means just you
