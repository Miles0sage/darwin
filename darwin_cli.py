#!/usr/bin/env python3
"""darwin — beautiful CLI for Darwin Commons.

Subcommands:
  darwin demo                scripted asciinema-safe walkthrough
  darwin triage              interactive quarantine queue (a/r/s/d/q keys)
  darwin browse [hash]       fingerprint explorer with syntax-highlighted diff
  darwin stats               contributor leaderboard + failure-shape sparklines
  darwin badge <hash|--mine> 3-line unicode contributor card
  darwin submit <file>       publish a fingerprint locally to staging
  darwin init                first-run wizard (GPG detect, banner, tutorial)
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ---------- paths ----------
COMMONS_REPO = Path(
    os.environ.get(
        "DARWIN_COMMONS_REPO",
        "/root/claude-code-agentic/darwin-mvp/darwin-commons",
    )
)
FINGERPRINTS = COMMONS_REPO / "fingerprints.jsonl"
QUARANTINE = COMMONS_REPO / "quarantine.jsonl"
STAGING = Path(
    os.environ.get(
        "DARWIN_STAGING_DIR",
        "/root/claude-code-agentic/darwin-mvp/staging",
    )
)
SESSION_DIR = Path(os.environ.get("DARWIN_SESSION_DIR", Path.home() / ".darwin"))
CHECKPOINT = SESSION_DIR / "triage-session.json"

# ---------- theme ----------
THEME = Theme(
    {
        "brand": "bold #7dd3fc",
        "accent": "#a78bfa",
        "ok": "bold #86efac",
        "warn": "bold #fcd34d",
        "err": "bold #fca5a5",
        "mute": "dim #94a3b8",
        "fp": "#fbbf24",
        "exc": "#f472b6",
        "hash": "italic #38bdf8",
        "key": "bold #f472b6",
        "bar": "#22d3ee",
    }
)
console = Console(theme=THEME, highlight=False)

# ---------- banner ----------
BANNER_LINES = [
    "    ┏┓       •  ",
    "    ┃┃┏┓┏┓┓┏┏┓┏┓",
    "    ┻┛┗┻┛ ┗┻┛┗┛┗",
]
GRADIENT = ["#7dd3fc", "#60a5fa", "#a78bfa", "#c084fc", "#f472b6"]


def _gradient(text: str, colors: list[str] = GRADIENT) -> Text:
    if not text:
        return Text("")
    t = Text()
    n = len(text)
    for i, ch in enumerate(text):
        c = colors[min(int((i / max(n - 1, 1)) * (len(colors) - 1)), len(colors) - 1)]
        t.append(ch, style=c)
    return t


def banner(subtitle: str = "the failure corpus that heals itself") -> Group:
    lines = [Align.center(_gradient(ln)) for ln in BANNER_LINES]
    sub = Align.center(Text(subtitle, style="mute"))
    return Group(*lines, sub)


# ---------- loaders ----------
@dataclass(frozen=True)
class Entry:
    fingerprint: str
    error_class: str
    raw: dict

    @property
    def short(self) -> str:
        return self.fingerprint[:12] if self.fingerprint else "?"

    @property
    def signature(self) -> str:
        return self.raw.get("signature") or self.raw.get("normalized_signature") or ""

    @property
    def contributor(self) -> str:
        p = self.raw.get("provenance") or {}
        return self.raw.get("contributor_hash") or p.get("contributor_hash") or ""

    @property
    def published_at(self) -> str:
        p = self.raw.get("provenance") or {}
        g = self.raw.get("generator") or {}
        ts = (
            self.raw.get("published_at")
            or p.get("published_at")
            or g.get("timestamp")
            or ""
        )
        if ts and "T" in ts and len(ts) >= 15 and "-" not in ts[:10]:
            try:
                return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"
            except Exception:
                return ts
        return ts

    @property
    def transformer_src(self) -> str:
        if self.raw.get("transformer_src"):
            return self.raw["transformer_src"]
        tp = self.raw.get("transformer_path")
        if tp:
            p = COMMONS_REPO / tp
            if p.exists():
                try:
                    return p.read_text()
                except Exception:
                    return ""
        return ""


def _read_jsonl(p: Path) -> list[Entry]:
    if not p.exists():
        return []
    out: list[Entry] = []
    for ln in p.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        out.append(
            Entry(
                fingerprint=d.get("fingerprint", ""),
                error_class=d.get("error_class", "?"),
                raw=d,
            )
        )
    return out


def _write_jsonl(p: Path, entries: list[Entry]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(e.raw) for e in entries) + ("\n" if entries else "")
    )


def _contributor_short(h: str) -> str:
    return h[:8] if h else "anon"


# ---------- typer app ----------
app = typer.Typer(
    help="beautiful CLI for Darwin Commons — the failure corpus that heals itself.",
    add_completion=False,
    rich_markup_mode="rich",
)


# ---------- triage ----------
def _render_entry(e: Entry, idx: int, total: int, accepted: int, rejected: int) -> Group:
    q = e.raw.get("quarantine", {}) or {}
    reason = q.get("reason", "unknown")
    sig = e.signature[:600]
    xform_src = (e.transformer_src or "# (no transformer body present)").strip()

    header = Table.grid(expand=True, padding=(0, 1))
    header.add_column(ratio=1)
    header.add_column(justify="right")
    header.add_row(
        Text.assemble(
            ("fp ", "mute"),
            (e.short, "fp"),
            ("  ", ""),
            (e.error_class, "exc"),
        ),
        Text(f"{idx + 1}/{total}  ✓{accepted}  ✗{rejected}", style="mute"),
    )

    sig_panel = Panel(
        Syntax(sig or "(empty signature)", "pytb", theme="ansi_dark", word_wrap=True),
        title="[mute]signature[/mute]",
        border_style="mute",
        padding=(0, 1),
    )
    xform_panel = Panel(
        Syntax(xform_src, "python", theme="ansi_dark", word_wrap=True),
        title="[mute]proposed transformer[/mute]",
        border_style="accent",
        padding=(0, 1),
    )
    reason_line = Text.assemble(
        ("gate: ", "mute"),
        (reason, "err"),
    )
    keys = Text.assemble(
        ("[a]", "key"), ("ccept  ", "mute"),
        ("[r]", "key"), ("eject  ", "mute"),
        ("[s]", "key"), ("kip  ", "mute"),
        ("[d]", "key"), ("iff  ", "mute"),
        ("[q]", "key"), ("uit", "mute"),
    )
    pct = int((idx / max(total, 1)) * 40)
    bar = Text("▰" * pct + "▱" * (40 - pct), style="bar")

    return Group(
        header,
        Rule(style="mute"),
        sig_panel,
        xform_panel,
        Padding(reason_line, (0, 1)),
        Rule(style="mute"),
        Padding(bar, (0, 1)),
        Padding(keys, (0, 1)),
    )


def _read_key() -> str:
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch
    except Exception:
        return sys.stdin.readline().strip()[:1]


def _save_checkpoint(idx: int, accepted: int, rejected: int, skipped: int) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(
        json.dumps(
            {
                "idx": idx,
                "accepted": accepted,
                "rejected": rejected,
                "skipped": skipped,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )


def _load_checkpoint() -> dict | None:
    if not CHECKPOINT.exists():
        return None
    try:
        return json.loads(CHECKPOINT.read_text())
    except Exception:
        return None


@app.command(help="interactive quarantine queue — vim keys.")
def triage(
    resume: bool = typer.Option(False, "--resume", help="resume from last checkpoint"),
) -> None:
    entries = _read_jsonl(QUARANTINE)
    if not entries:
        console.print(Panel.fit(
            Text.assemble(
                ("no quarantined entries — ", "ok"),
                ("the queue is clean.", "mute"),
            ),
            border_style="ok",
        ))
        return

    idx, accepted, rejected, skipped = 0, 0, 0, 0
    if resume:
        cp = _load_checkpoint()
        if cp:
            idx = cp.get("idx", 0)
            accepted = cp.get("accepted", 0)
            rejected = cp.get("rejected", 0)
            skipped = cp.get("skipped", 0)
            console.print(f"[mute]resumed at {idx}/{len(entries)}[/mute]")

    accepted_entries: list[Entry] = []
    surviving: list[Entry] = []

    def _handle_sigint(signum, frame):
        _save_checkpoint(idx, accepted, rejected, skipped)
        console.print(
            "\n[warn]paused[/warn] — [mute]resume with[/mute] [key]darwin triage --resume[/key]"
        )
        sys.exit(130)

    signal.signal(signal.SIGINT, _handle_sigint)

    while idx < len(entries):
        e = entries[idx]
        console.clear()
        console.print(banner())
        console.print()
        console.print(_render_entry(e, idx, len(entries), accepted, rejected))
        console.print()
        console.print("[mute]press a key:[/mute] ", end="")
        sys.stdout.flush()
        k = _read_key().lower()

        if k == "a":
            accepted += 1
            entry_no_q = Entry(e.fingerprint, e.error_class, {k: v for k, v in e.raw.items() if k != "quarantine"})
            accepted_entries.append(entry_no_q)
            idx += 1
        elif k == "r":
            rejected += 1
            idx += 1
        elif k == "s":
            skipped += 1
            surviving.append(e)
            idx += 1
        elif k == "d":
            console.print()
            console.print(Syntax(json.dumps(e.raw, indent=2), "json", theme="ansi_dark"))
            console.print("[mute]press any key to return[/mute]")
            _read_key()
        elif k == "q":
            _save_checkpoint(idx, accepted, rejected, skipped)
            console.print("\n[warn]quit[/warn] — [mute]progress saved.[/mute]")
            return
        else:
            continue

    surviving.extend(entries[idx:])
    _write_jsonl(QUARANTINE, surviving)
    if accepted_entries:
        with FINGERPRINTS.open("a") as f:
            for e in accepted_entries:
                f.write(json.dumps(e.raw) + "\n")

    if CHECKPOINT.exists():
        CHECKPOINT.unlink()

    console.clear()
    console.print(banner())
    summary = Panel(
        Text.assemble(
            ("triage complete\n\n", "ok"),
            (f"  ✓ accepted  {accepted}\n", "ok"),
            (f"  ✗ rejected  {rejected}\n", "err"),
            (f"  ◦ skipped   {skipped}\n", "mute"),
        ),
        title="[brand]darwin triage[/brand]",
        border_style="brand",
    )
    console.print(Padding(summary, (1, 2)))


# ---------- browse ----------
@app.command(help="fingerprint explorer — hash lookup or list.")
def browse(
    hash: Optional[str] = typer.Argument(None, help="fingerprint prefix (optional)"),
    limit: int = typer.Option(20, "--limit", "-n", help="max rows"),
) -> None:
    entries = _read_jsonl(FINGERPRINTS)
    if hash:
        matches = [e for e in entries if e.fingerprint.startswith(hash)]
        if not matches:
            console.print(f"[err]no match for[/err] [fp]{hash}[/fp]")
            raise typer.Exit(1)
        e = matches[0]
        console.print(banner())
        console.print()
        info = Table.grid(padding=(0, 2))
        info.add_column(style="mute")
        info.add_column()
        info.add_row("fingerprint", Text(e.fingerprint, style="fp"))
        info.add_row("error_class", Text(e.error_class, style="exc"))
        info.add_row("contributor", Text(_contributor_short(e.contributor), style="hash"))
        info.add_row("published_at", Text(e.published_at or "?", style="mute"))
        console.print(Panel(info, border_style="accent", title="[brand]fingerprint[/brand]"))
        sig = e.signature[:1200]
        if sig:
            console.print(Panel(Syntax(sig, "pytb", theme="ansi_dark"), title="[mute]signature[/mute]", border_style="mute"))
        xform = e.transformer_src
        if xform:
            console.print(Panel(Syntax(xform, "python", theme="ansi_dark"), title="[mute]transformer[/mute]", border_style="accent"))
        return

    console.print(banner(f"{len(entries)} fingerprints in corpus"))
    console.print()
    t = Table(
        show_header=True,
        header_style="brand",
        border_style="mute",
        box=None,
        expand=True,
        pad_edge=False,
    )
    t.add_column("#", style="mute", width=4)
    t.add_column("fp", style="fp")
    t.add_column("error", style="exc")
    t.add_column("contributor", style="hash")
    t.add_column("at", style="mute")
    for i, e in enumerate(entries[:limit]):
        t.add_row(
            str(i),
            e.short,
            e.error_class,
            _contributor_short(e.contributor),
            (e.published_at or "")[:10],
        )
    console.print(t)
    if len(entries) > limit:
        console.print(f"[mute]...{len(entries) - limit} more. use --limit to see more.[/mute]")


# ---------- badge ----------
def _render_badge(contributor: str, count: int, rank: int | None, first_at: str) -> Panel:
    top = Text()
    top.append("▛", style="brand")
    top.append(" DARWIN COMMONS CONTRIBUTOR ", style="bold")
    top.append("▜", style="brand")
    body = Table.grid(padding=(0, 2))
    body.add_column(style="mute")
    body.add_column()
    body.add_row("ident", Text(contributor, style="hash"))
    body.add_row("fingerprints", Text(str(count), style="ok"))
    body.add_row("rank", Text(f"#{rank}" if rank else "unranked", style="accent"))
    body.add_row("since", Text(first_at[:10] if first_at else "?", style="mute"))
    return Panel(body, border_style="brand", title=top, padding=(0, 2))


@app.command(help="contributor badge — 3-line terminal card.")
def badge(
    hash: Optional[str] = typer.Argument(None, help="contributor hash prefix"),
    mine: bool = typer.Option(False, "--mine", help="use $DARWIN_ME env var"),
) -> None:
    if mine:
        hash = os.environ.get("DARWIN_ME")
        if not hash:
            console.print("[err]DARWIN_ME not set.[/err] [mute]export DARWIN_ME=<your_contributor_hash>[/mute]")
            raise typer.Exit(1)
    if not hash:
        console.print("[err]missing contributor hash.[/err] [mute]usage: darwin badge <hash> | --mine[/mute]")
        raise typer.Exit(1)

    entries = _read_jsonl(FINGERPRINTS)
    by_contrib: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for e in entries:
        c = e.contributor
        if not c:
            continue
        by_contrib[c] += 1
        ts = e.published_at
        if c not in first_seen or (ts and ts < first_seen[c]):
            first_seen[c] = ts

    matches = [c for c in by_contrib if c.startswith(hash)]
    if not matches:
        console.print(f"[err]no contributor matches[/err] [hash]{hash}[/hash]")
        raise typer.Exit(1)
    c = matches[0]

    ranked = sorted(by_contrib.items(), key=lambda kv: -kv[1])
    rank = next((i + 1 for i, (k, _) in enumerate(ranked) if k == c), None)
    console.print()
    console.print(Padding(_render_badge(_contributor_short(c), by_contrib[c], rank, first_seen.get(c, "")), (1, 2)))


# ---------- stats ----------
def _sparkline(values: list[int]) -> Text:
    if not values or max(values) == 0:
        return Text("·" * len(values), style="mute")
    blocks = "▁▂▃▄▅▆▇█"
    mx = max(values)
    t = Text()
    for v in values:
        idx = int((v / mx) * (len(blocks) - 1))
        t.append(blocks[idx], style="bar")
    return t


@app.command(help="contributor leaderboard + failure-shape sparklines.")
def stats() -> None:
    entries = _read_jsonl(FINGERPRINTS)
    if not entries:
        console.print("[mute]corpus is empty.[/mute]")
        return

    by_class: Counter[str] = Counter(e.error_class for e in entries)
    by_contrib: Counter[str] = Counter()
    by_day: defaultdict[str, int] = defaultdict(int)
    for e in entries:
        c = e.contributor
        if c:
            by_contrib[c] += 1
        d = (e.published_at or "")[:10]
        if d:
            by_day[d] += 1

    today = datetime.now(timezone.utc).date()
    last_14 = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    daily_counts = [by_day.get(d, 0) for d in last_14]

    console.print(banner(f"{len(entries)} fingerprints · {len(by_class)} error classes · {len(by_contrib)} contributors"))
    console.print()

    top_classes = Table(
        show_header=True,
        header_style="brand",
        border_style="mute",
        box=None,
        title="[brand]top error shapes[/brand]",
        title_justify="left",
    )
    top_classes.add_column("rank", style="mute", width=4)
    top_classes.add_column("class", style="exc")
    top_classes.add_column("count", style="ok", justify="right", width=6)
    top_classes.add_column("share", width=30)
    total = sum(by_class.values())
    for i, (cls, n) in enumerate(by_class.most_common(10)):
        pct = n / total
        bar = "█" * max(int(pct * 24), 1)
        top_classes.add_row(
            str(i + 1),
            cls,
            str(n),
            Text(bar, style="bar") + Text(f" {pct:.0%}", style="mute"),
        )

    top_contrib = Table(
        show_header=True,
        header_style="brand",
        border_style="mute",
        box=None,
        title="[brand]top contributors[/brand]",
        title_justify="left",
    )
    top_contrib.add_column("rank", style="mute", width=4)
    top_contrib.add_column("ident", style="hash")
    top_contrib.add_column("count", style="ok", justify="right", width=6)
    for i, (c, n) in enumerate(by_contrib.most_common(10)):
        top_contrib.add_row(str(i + 1), _contributor_short(c), str(n))

    console.print(Columns([top_classes, top_contrib], padding=(0, 4), expand=True))
    console.print()

    spark_panel = Panel(
        Group(
            Text.assemble(("last 14 days  ", "mute"), _sparkline(daily_counts)),
            Text(f"  min {min(daily_counts)} · max {max(daily_counts)} · sum {sum(daily_counts)}", style="mute"),
        ),
        border_style="accent",
        title="[brand]corpus growth[/brand]",
        title_align="left",
    )
    console.print(spark_panel)


# ---------- submit ----------
@app.command(help="stage a fingerprint locally for sync.")
def submit(
    file: Path = typer.Argument(..., help="path to fingerprint JSON"),
) -> None:
    if not file.exists():
        console.print(f"[err]file not found:[/err] {file}")
        raise typer.Exit(1)
    try:
        payload = json.loads(file.read_text())
    except Exception as e:
        console.print(f"[err]invalid JSON:[/err] {e}")
        raise typer.Exit(1)
    if not payload.get("fingerprint"):
        console.print("[err]missing required field[/err] [fp]fingerprint[/fp]")
        raise typer.Exit(1)
    STAGING.mkdir(parents=True, exist_ok=True)
    out = STAGING / "pending.jsonl"
    payload.setdefault("published_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault(
        "contributor_hash",
        hashlib.sha256((os.environ.get("DARWIN_ME", "anonymous")).encode()).hexdigest()[:16],
    )
    with out.open("a") as f:
        f.write(json.dumps(payload) + "\n")
    console.print(
        Panel(
            Text.assemble(
                ("staged   ", "ok"), (str(out), "mute"), ("\n", ""),
                ("fp       ", "mute"), (payload["fingerprint"][:16], "fp"), ("\n", ""),
                ("next     ", "mute"), ("wait for the sync timer (every 15m) or run commons_sync.py manually", "accent"),
            ),
            border_style="ok",
            title="[brand]submitted[/brand]",
        )
    )


# ---------- init ----------
@app.command(help="first-run wizard.")
def init() -> None:
    console.print(banner())
    console.print()
    gpg = subprocess.run(["which", "gpg"], capture_output=True, text=True).stdout.strip()
    has_gpg = bool(gpg)
    has_repo = COMMONS_REPO.exists()
    n = len(_read_jsonl(FINGERPRINTS))

    checks = Table.grid(padding=(0, 2))
    checks.add_column()
    checks.add_column()
    checks.add_row(Text("✓" if has_gpg else "·", style="ok" if has_gpg else "warn"), Text("gpg installed" if has_gpg else "gpg not found — signing disabled"))
    checks.add_row(Text("✓" if has_repo else "·", style="ok" if has_repo else "warn"), Text(f"commons repo at {COMMONS_REPO}"))
    checks.add_row(Text("✓" if n else "·", style="ok" if n else "warn"), Text(f"{n} fingerprints loaded"))
    console.print(Panel(checks, title="[brand]environment check[/brand]", border_style="brand"))
    console.print()
    console.print("[mute]next steps:[/mute]")
    console.print("  [key]darwin stats[/key]           overview of the corpus")
    console.print("  [key]darwin browse[/key]          latest fingerprints")
    console.print("  [key]darwin triage[/key]          review quarantined submissions")
    console.print("  [key]darwin demo[/key]            watch the flywheel in 60s")


# ---------- demo (scripted, asciinema-clean) ----------
def _type(text: str, delay: float = 0.012) -> None:
    for ch in text:
        console.print(ch, end="", highlight=False, soft_wrap=True)
        sys.stdout.flush()
        time.sleep(delay)
    console.print()


def _prompt(prompt: str = "$ ") -> None:
    console.print(f"[accent]{prompt}[/accent]", end="")
    sys.stdout.flush()


def _pause(s: float = 0.6) -> None:
    time.sleep(s)


@app.command(help="scripted walkthrough — asciinema-safe, no backslash hell.")
def demo(
    speed: float = typer.Option(1.0, "--speed", help="playback speed (higher = faster)"),
) -> None:
    d = 0.012 / speed
    p = 0.6 / speed
    console.clear()
    console.print(banner("60-second tour"))
    console.print()
    _pause(p * 2)

    _prompt()
    _type("darwin stats", d)
    _pause(p)
    stats()
    _pause(p * 3)

    console.print()
    _prompt()
    _type("darwin browse", d)
    _pause(p)
    browse(hash=None, limit=8)
    _pause(p * 3)

    entries = _read_jsonl(FINGERPRINTS)
    if entries:
        sample = entries[0]
        console.print()
        _prompt()
        _type(f"darwin browse {sample.short}", d)
        _pause(p)
        browse(hash=sample.short)
        _pause(p * 3)

        contrib = sample.contributor
        if contrib:
            console.print()
            _prompt()
            _type(f"darwin badge {_contributor_short(contrib)}", d)
            _pause(p)
            badge(hash=_contributor_short(contrib), mine=False)
            _pause(p * 3)

    console.print()
    console.print(
        Padding(
            Panel(
                Text.assemble(
                    ("the failure corpus that heals itself.\n\n", "brand"),
                    ("contribute     ", "mute"), ("POST /darwin/heal/public\n", "accent"),
                    ("browse         ", "mute"), ("github.com/Miles0sage/darwin-commons\n", "accent"),
                    ("install        ", "mute"), ("pip install darwin-commons (soon)\n", "accent"),
                ),
                border_style="brand",
                title="[brand]darwin commons[/brand]",
            ),
            (1, 2),
        )
    )
    _pause(p * 2)


# ---------- default command = short dashboard ----------
@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    entries = _read_jsonl(FINGERPRINTS)
    n = len(entries)
    quarantined = len(_read_jsonl(QUARANTINE))
    contributors = len({e.contributor for e in entries if e.contributor})
    classes = len({e.error_class for e in entries})
    console.print(banner())
    console.print()
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    def card(label: str, value: str, style: str) -> Panel:
        return Panel(
            Align.center(Text(value, style=style)),
            subtitle=f"[mute]{label}[/mute]",
            border_style="mute",
            width=22,
        )
    grid.add_row(
        card("fingerprints", str(n), "ok"),
        card("error classes", str(classes), "exc"),
        card("contributors", str(contributors), "hash"),
        card("quarantine", str(quarantined), "warn" if quarantined else "mute"),
    )
    console.print(grid)
    console.print()
    console.print(Padding(Text.assemble(
        ("try ", "mute"), ("darwin stats", "key"), ("  |  ", "mute"),
        ("darwin browse", "key"), ("  |  ", "mute"),
        ("darwin demo", "key"),
    ), (0, 2)))


# ---------- evo (DGM engine) ----------
evo_app = typer.Typer(help="evolutionary engine — archive, mutate, evaluate, gate.", rich_markup_mode="rich")
app.add_typer(evo_app, name="evo")


def _evo_mod():
    import evo as _e
    return _e


@evo_app.command("init", help="seed archive with baseline variant.")
def evo_init(force: bool = typer.Option(False, "--force")) -> None:
    e = _evo_mod()
    arch = e.Archive()
    arch.ensure()
    if arch.list() and not force:
        console.print("[warn]archive not empty[/warn] — use --force to re-seed")
        raise typer.Exit(1)
    seed = e.Variant.new(config=e.seed_config(), parent=None, notes="baseline seed")
    arch.add(seed)
    console.print(Panel(
        Text.assemble(
            ("seeded  ", "ok"), (seed.id, "fp"), ("\n", ""),
            ("archive ", "mute"), (str(arch.root), "accent"), ("\n", ""),
            ("next    ", "mute"), ("darwin evo score " + seed.id, "key"),
        ),
        border_style="ok", title="[brand]evo init[/brand]",
    ))


@evo_app.command("list", help="list variants in archive.")
def evo_list() -> None:
    e = _evo_mod()
    variants = e.Archive().list()
    if not variants:
        console.print("[mute]archive empty — run[/mute] [key]darwin evo init[/key]")
        return
    console.print(banner(f"{len(variants)} variants · {sum(1 for v in variants if v.score is not None)} scored"))
    t = Table(show_header=True, header_style="brand", border_style="mute", box=None, expand=True)
    t.add_column("id", style="fp", width=11)
    t.add_column("gen", style="mute", width=4)
    t.add_column("parent", style="hash", width=11)
    t.add_column("score", style="ok", justify="right", width=6)
    t.add_column("kids", style="mute", justify="right", width=4)
    t.add_column("notes", style="mute")
    for v in sorted(variants, key=lambda x: (x.generation, x.id)):
        score = f"{v.score:.2f}" if v.score is not None else "—"
        t.add_row(v.id, str(v.generation), v.parent_id or "·", score, str(len(v.children)), (v.notes or "")[:60])
    console.print(t)


@evo_app.command("show", help="inspect one variant.")
def evo_show(id: str) -> None:
    e = _evo_mod()
    try:
        v = e.Archive().get(id)
    except Exception:
        console.print(f"[err]not found:[/err] {id}")
        raise typer.Exit(1)
    console.print(banner())
    info = Table.grid(padding=(0, 2))
    info.add_column(style="mute")
    info.add_column()
    info.add_row("id", Text(v.id, style="fp"))
    info.add_row("parent", Text(v.parent_id or "·", style="hash"))
    info.add_row("generation", Text(str(v.generation), style="accent"))
    info.add_row("score", Text(f"{v.score:.3f}" if v.score is not None else "unscored", style="ok" if v.score else "warn"))
    info.add_row("children", Text(", ".join(v.children) or "·", style="mute"))
    info.add_row("created", Text(v.created_at, style="mute"))
    info.add_row("notes", Text(v.notes or "·", style="mute"))
    console.print(Panel(info, border_style="accent", title="[brand]variant[/brand]"))
    console.print(Panel(Syntax(json.dumps(v.config, indent=2), "json", theme="ansi_dark"), title="[mute]config[/mute]", border_style="mute"))
    if v.score_breakdown:
        console.print(Panel(Syntax(json.dumps(v.score_breakdown, indent=2)[:4000], "json", theme="ansi_dark"), title="[mute]score breakdown[/mute]", border_style="mute"))


@evo_app.command("score", help="evaluate a variant against full scenario set.")
def evo_score(
    id: str,
    fleet_size: int = typer.Option(3, "--fleet-size", "-n"),
    cap: int = typer.Option(0, "--cap", help="max bugs per corpus (0 = all)"),
) -> None:
    e = _evo_mod()
    arch = e.Archive()
    try:
        v = arch.get(id)
    except Exception:
        console.print(f"[err]not found:[/err] {id}")
        raise typer.Exit(1)
    corps = v.config.get("corpora", [])
    with Progress(SpinnerColumn(), TextColumn("[mute]{task.description}[/mute]"), console=console, transient=True) as p:
        p.add_task(f"evaluating {v.id} on corpora {corps} (no-cache)...", total=None)
        score, breakdown = e.evaluate(v, max_bugs_per_corpus=(cap or None))
    v.score = score
    v.score_breakdown.update(breakdown)
    arch.update(v)
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style="mute")
    tbl.add_column()
    tbl.add_row("score", Text(f"{score:.3f}", style="ok" if score >= 0.8 else "warn"))
    tbl.add_row("healed", Text(f"{breakdown.get('total_healed', 0)}/{breakdown.get('total_attempted', 0)}", style="accent"))
    tbl.add_row("duration", Text(f"{breakdown.get('duration_s', 0)}s", style="mute"))
    for name, r in breakdown.get("corpora", {}).items():
        if "error" in r:
            tbl.add_row(f"  {name}", Text(r["error"], style="err"))
            continue
        pr = r.get("pass_rate", 0.0)
        sty = "ok" if pr >= 0.8 else ("warn" if pr > 0 else "err")
        tbl.add_row(f"  {name}", Text(f"{r.get('healed',0)}/{r.get('attempted',0)}  {pr:.0%}", style=sty))
    console.print(Panel(tbl, title=f"[brand]score[/brand] [fp]{v.id}[/fp]", border_style="brand"))


@evo_app.command("step", help="one DGM loop iteration: sample → mutate → gate → archive.")
def evo_step() -> None:
    e = _evo_mod()
    arch = e.Archive()
    with Progress(SpinnerColumn(), TextColumn("[mute]{task.description}[/mute]"), console=console, transient=True) as p:
        p.add_task("sample → mutate → gate → evaluate...", total=None)
        report = e.step(arch)
    status = report.get("status", "?")
    color = {"accepted": "ok", "rejected": "warn", "mutation_failed": "err", "no_parent": "err"}.get(status, "mute")
    console.print(Panel(
        Syntax(json.dumps(report, indent=2), "json", theme="ansi_dark"),
        title=f"[{color}]{status}[/{color}]",
        border_style=color,
    ))


@evo_app.command("tree", help="population tree with scores.")
def evo_tree() -> None:
    e = _evo_mod()
    variants = {v.id: v for v in e.Archive().list()}
    if not variants:
        console.print("[mute]archive empty.[/mute]")
        return
    roots = [v for v in variants.values() if not v.parent_id]
    console.print(banner(f"{len(variants)} variants across {1 + max((v.generation for v in variants.values()), default=0)} generations"))
    console.print()

    def render(v, prefix="", last=True) -> None:
        score = f" [{('ok' if (v.score or 0) >= 0.8 else 'warn')}]{v.score:.2f}[/]" if v.score is not None else " [mute]—[/mute]"
        branch = "└─ " if last else "├─ "
        console.print(f"{prefix}{branch}[fp]{v.id}[/fp] gen{v.generation}{score} [mute]{(v.notes or '')[:50]}[/mute]")
        kids = [variants[c] for c in v.children if c in variants]
        for i, kid in enumerate(kids):
            render(kid, prefix + ("   " if last else "│  "), i == len(kids) - 1)

    for i, root in enumerate(roots):
        render(root, "", i == len(roots) - 1)


if __name__ == "__main__":
    app()
