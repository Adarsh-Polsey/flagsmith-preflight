import getpass
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .state import DIR


def who() -> str:
    if os.environ.get("PREFLIGHT_USER"):
        return os.environ["PREFLIGHT_USER"]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
    except OSError:
        name = ""
    return name or getpass.getuser()


def record(root: Path, stage: str, action: str, answer: str, via: str, note: str = "") -> dict:
    entry = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "who": who(),
        "stage": stage,
        "action": action,
        "answer": answer,
        "via": via,
    }
    if note:
        entry["note"] = note
    (root / DIR).mkdir(exist_ok=True)
    with open(root / DIR / "approvals.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def confirm(root: Path, stage: str, question: str, action: str, approved: bool = False, note: str = "") -> bool:
    """Ask a human. `approved` means a human already said yes elsewhere (in the agent chat); `note` quotes them."""
    if approved:
        if not note:
            print("--yes needs --note with the human's words, so the log shows who approved what.", file=sys.stderr)
            return False
        record(root, stage, action, "y", "chat", note)
        return True
    if not sys.stdin.isatty():
        print(f"{question} [y/N] needs a human: run this in a terminal, or pass --yes --note after they approve.", file=sys.stderr)
        return False
    answer = input(f"{question} [y/N] ").strip().lower()
    yes = answer in ("y", "yes")
    record(root, stage, action, "y" if yes else "n", "prompt")
    return yes
