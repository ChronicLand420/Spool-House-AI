from __future__ import annotations

import re
from datetime import date
from pathlib import Path


SESSION_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-session-(?P<session>\d{3})\.md$")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    devlog_dir = repo_root / "docs" / "devlog"
    devlog_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    session_number = _next_session_number(devlog_dir, today)
    path = devlog_dir / f"{today}-session-{session_number:03d}.md"
    path.write_text(_template(today, session_number), encoding="utf-8")
    print(path)


def _next_session_number(devlog_dir: Path, today: str) -> int:
    highest = 0
    for path in devlog_dir.glob(f"{today}-session-*.md"):
        match = SESSION_PATTERN.match(path.name)
        if match and match.group("date") == today:
            highest = max(highest, int(match.group("session")))
    return highest + 1


def _template(today: str, session_number: int) -> str:
    session = f"{session_number:03d}"
    return f"""# SHAI Devlog - {today} - Session {session}

## Summary

Short paragraph describing what changed.

## Why

Why this work was done.

## Files Changed

- `path/to/file`: Short explanation.

## Features Added

- None.

## Bugs Fixed

- None.

## Tests Run

- `command`: Not run yet.

## Known Issues

- None.

## Next Suggested Steps

- None.
"""


if __name__ == "__main__":
    main()
