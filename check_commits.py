"""Run what CI runs, on EVERY commit you are about to push.

    python check_commits.py                 # origin/main..HEAD
    python check_commits.py origin/main~5   # everything after that commit

Why per commit and not just the last one: splitting work into many small commits is good, but if the code lands in one
commit and the test that covers it in the next, the commit in between is broken -- and GitHub marks it with a red X and
e-mails you about it, even though the branch tip is fine. This checks each one in a throwaway worktree, so nothing in
your working copy is touched.

Checks per commit, the same three the `python` and `dwaniforms-ui` jobs run:
    pytest -q
    python -m dwaniforms.langpacks check
    the language packs each parse as JSON
Exit code 0 if every commit passes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    out = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode == 0, (out[-1] if out else "")


def check(tree: Path) -> list[str]:
    bad = []
    ok, last = run([sys.executable, "-m", "pytest", "-q"], tree)
    if not ok:
        bad.append("pytest: " + last)
    ok, last = run([sys.executable, "-m", "dwaniforms.langpacks", "check"], tree)
    if not ok:
        bad.append("langpacks check: " + last)
    for f in sorted((tree / "dwaniforms" / "lang").glob("*.json")):
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:                       # a half-saved pack is easy to miss and breaks every language
            bad.append(f"{f.name}: {e}")
    return bad


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parent
    base = argv[0] if argv else "origin/main"
    try:
        shas = git("rev-list", "--reverse", f"{base}..HEAD", cwd=repo).split()
    except subprocess.CalledProcessError:
        print(f"Can't list commits after {base!r}. Try: python check_commits.py HEAD~5")
        return 2
    if not shas:
        print(f"No commits after {base}. Nothing to check.")
        return 0

    work = Path(tempfile.mkdtemp(prefix="dwani-check-"))
    tree = work / "t"
    failed = 0
    try:
        git("worktree", "add", "--detach", "-q", str(tree), shas[0], cwd=repo)
        for sha in shas:
            git("-C", str(tree), "checkout", "--detach", "-q", sha, cwd=repo)
            subject = git("log", "-1", "--format=%s", sha, cwd=repo)
            bad = check(tree)
            failed += bool(bad)
            print(("FAIL " if bad else "ok   ") + sha[:7] + "  " + subject)
            for line in bad:
                print("        " + line)
    finally:
        git("worktree", "remove", "--force", str(tree), cwd=repo)
        shutil.rmtree(work, ignore_errors=True)

    print()
    if failed:
        print(f"{failed} of {len(shas)} commits would be red on GitHub.")
        print("Fix: put the test in the same commit as the code it covers, or reorder before pushing.")
        return 1
    print(f"All {len(shas)} commits pass. Safe to push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
