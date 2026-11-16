"""Generate weekly activity reports straight from git history.

    python scripts/weekly_report.py                 # write every week to reports/weekly/
    python scripts/weekly_report.py --stdout        # print to the terminal instead
    python scripts/weekly_report.py --since 2026-11-01

Pure standard library plus the git binary — no token, no API calls, so it runs anywhere a
checkout exists, including a GitHub Actions runner. Weeks with no commits produce no file:
an empty week is a fact worth noticing, not a template to fill in.
"""
from __future__ import annotations

import argparse
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

IST = ZoneInfo("Asia/Kolkata")  # commits arrive with mixed offsets; report them all in IST

AREAS = {
    "src/": "Model and pipeline code",
    "api/": "Serving layer",
    "data/": "Data acquisition and prep",
    "notebooks/": "Notebooks and experiments",
    "tests/": "Tests",
    "docs/": "Documentation",
    ".github/": "CI and automation",
    "configs/": "Configuration",
}


@dataclass
class Commit:
    sha: str
    when: datetime
    author: str
    subject: str
    files: int = 0
    added: int = 0
    removed: int = 0
    paths: list[str] = field(default_factory=list)


@dataclass
class Week:
    year: int
    week: int
    commits: list[Commit] = field(default_factory=list)

    @property
    def start(self) -> date:
        return date.fromisocalendar(self.year, self.week, 1)

    @property
    def end(self) -> date:
        return date.fromisocalendar(self.year, self.week, 7)

    @property
    def label(self) -> str:
        return f"{self.year}-W{self.week:02d}"

    def area_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for c in self.commits:
            for p in c.paths:
                for prefix, name in AREAS.items():
                    if p.startswith(prefix):
                        counts[name] += 1
                        break
                else:
                    counts["Repository root"] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def collect(since: str | None = None) -> list[Commit]:
    cmd = ["log", "--all", "--date=iso-strict", "--pretty=format:@@%H|%ad|%an|%s", "--numstat"]
    if since:
        cmd.insert(1, f"--since={since}")
    raw = git(*cmd)

    commits: list[Commit] = []
    for block in raw.split("@@"):
        block = block.strip("\n")
        if not block:
            continue
        head, _, body = block.partition("\n")
        sha, when, author, subject = head.split("|", 3)
        c = Commit(sha=sha[:7], when=datetime.fromisoformat(when).astimezone(IST), author=author, subject=subject)
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            c.files += 1
            c.added += int(added) if added.isdigit() else 0
            c.removed += int(removed) if removed.isdigit() else 0
            c.paths.append(path)
        commits.append(c)
    return commits


def bucket(commits: list[Commit]) -> list[Week]:
    weeks: dict[tuple[int, int], Week] = {}
    for c in commits:
        iso = c.when.isocalendar()
        key = (iso.year, iso.week)
        weeks.setdefault(key, Week(iso.year, iso.week)).commits.append(c)
    for w in weeks.values():
        w.commits.sort(key=lambda c: c.when)
    return [weeks[k] for k in sorted(weeks)]


def render(week: Week) -> str:
    commits = week.commits
    total_add = sum(c.added for c in commits)
    total_del = sum(c.removed for c in commits)
    # the same person can commit under two git identities — dedupe case-insensitively
    seen: dict[str, str] = {}
    for c in commits:
        seen.setdefault(c.author.lower().replace(" ", ""), c.author)
    authors = sorted(seen.values())
    active_days = sorted({c.when.date() for c in commits})
    biggest = max(commits, key=lambda c: c.added + c.removed)

    lines = [
        f"# Weekly activity report — {week.label}",
        "",
        f"**Period:** {week.start:%d %b %Y} to {week.end:%d %b %Y}  ",
        f"**Commits:** {len(commits)}  ·  **Active days:** {len(active_days)}  ·  "
        f"**Lines:** +{total_add} / −{total_del}  ",
        f"**Contributors:** {', '.join(authors)}" + ("  ·  *week in progress*" if week.end >= date.today() else ""),
        "",
        "## What moved",
        "",
        "| Area | Files touched |",
        "| --- | --- |",
    ]
    lines += [f"| {name} | {n} |" for name, n in week.area_counts().items()]

    lines += ["", "## Commits", "", "| Date | Author | Change | Files | ± |", "| --- | --- | --- | --- | --- |"]
    for c in commits:
        subject = c.subject.replace("|", "\\|")
        lines.append(f"| {c.when:%d %b %H:%M} | {c.author} | {subject} | {c.files} | +{c.added}/−{c.removed} |")

    lines += [
        "",
        "## Reading this week",
        "",
        f"- Largest single change: `{biggest.sha}` — {biggest.subject} (+{biggest.added}/−{biggest.removed}).",
        f"- Active on {len(active_days)} of 7 days ({', '.join(d.strftime('%a %d') for d in active_days)}).",
        f"- Most touched area: {next(iter(week.area_counts()))}.",
        "",
        "> Generated by `scripts/weekly_report.py` from git history. It reports what was committed, "
        "not what was attempted: uncommitted work does not appear here.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("reports/weekly"))
    ap.add_argument("--since", default=None, help="ISO date, e.g. 2026-11-01")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing files")
    args = ap.parse_args()

    commits = collect(args.since)
    if not commits:
        raise SystemExit("no commits found — is this a git checkout?")

    weeks = bucket(commits)
    if args.stdout:
        print("\n\n---\n\n".join(render(w) for w in weeks))
        return

    args.out.mkdir(parents=True, exist_ok=True)
    for w in weeks:
        path = args.out / f"{w.label}.md"
        path.write_text(render(w))
        print(f"wrote {path} ({len(w.commits)} commits)")

    index = ["# Weekly reports", "",
             f"Generated {date.today():%d %b %Y} from `git log`. Re-run `python scripts/weekly_report.py` to refresh.", "",
             "| Week | Period | Commits | Lines |", "| --- | --- | --- | --- |"]
    for w in weeks:
        add = sum(c.added for c in w.commits)
        dele = sum(c.removed for c in w.commits)
        index.append(f"| [{w.label}]({w.label}.md) | {w.start:%d %b}–{w.end:%d %b %Y} | {len(w.commits)} | +{add}/−{dele} |")
    (args.out / "README.md").write_text("\n".join(index) + "\n")
    print(f"wrote {args.out / 'README.md'}")


if __name__ == "__main__":
    main()
