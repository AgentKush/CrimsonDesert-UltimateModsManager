"""Which lines does this PR ADD, and does any test execute them?

Not overall coverage -- coverage of the diff. An uncovered added line is
a line shipped on the strength of reasoning alone.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "upstream/master"
HEAD = sys.argv[2] if len(sys.argv) > 2 else "HEAD"


def added_lines(path: str) -> set[int]:
    """New-file line numbers this diff adds (not context, not deletions)."""
    out = subprocess.run(
        ["git", "diff", "-U0", f"{BASE}...{HEAD}", "--", path],
        capture_output=True, text=True, check=True).stdout
    hunks, cur, seen = set(), None, 0
    for line in out.splitlines():
        m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        if m:
            cur = int(m.group(1))
            seen = 0
            continue
        if cur is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            hunks.add(cur + seen)
            seen += 1
        elif line.startswith(" "):
            seen += 1
    return hunks


files = subprocess.run(
    ["git", "diff", "--name-only", f"{BASE}...{HEAD}", "--", "src/"],
    capture_output=True, text=True, check=True).stdout.split()

data = json.loads(Path("coverage.json").read_text(encoding="utf-8"))
by_file = {}
for k, v in data["files"].items():
    by_file[Path(k).as_posix().replace("\\", "/")] = v

total_added = total_uncovered = 0
print(f"{'file':<46} {'added':>6} {'run':>5} {'MISSED':>7}")
print("-" * 70)
for f in files:
    added = added_lines(f)
    key = next((k for k in by_file if k.endswith(f.split("src/")[-1])), None)
    if key is None:
        print(f"{f:<46} {len(added):>6}   (no coverage data)")
        continue
    ex = set(by_file[key]["executed_lines"])
    miss = set(by_file[key]["missing_lines"])
    # only count added lines that are executable at all
    executable = added & (ex | miss)
    uncovered = sorted(executable & miss)
    total_added += len(executable)
    total_uncovered += len(uncovered)
    print(f"{f:<46} {len(executable):>6} "
          f"{len(executable) - len(uncovered):>5} {len(uncovered):>7}")
    if uncovered:
        src = Path(f).read_text(encoding="utf-8").splitlines()
        for n in uncovered:
            print(f"        L{n}: {src[n - 1].strip()[:88]}")

print("-" * 70)
pct = 100.0 * (total_added - total_uncovered) / total_added if total_added else 100.0
print(f"added executable lines: {total_added}   covered: "
      f"{total_added - total_uncovered}   MISSED: {total_uncovered}   "
      f"({pct:.1f}%)")
