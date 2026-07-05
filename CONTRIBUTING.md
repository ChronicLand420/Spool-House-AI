# Contributing

Thanks for helping improve Spool House AI.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Checks

Run these before committing:

```powershell
python -m compileall spool_house_ai
python -m spool_house_ai.main --test
```

## Devlog Requirement

Every meaningful code change must include a devlog entry under `docs/devlog/`. Create it before starting work:

```powershell
python scripts/new_devlog.py
```

Fill in what changed, why it changed, important files touched, features added, bugs fixed, tests run, known issues, and next suggested steps before committing.

## Git Workflow

- Work from a branch.
- Keep generated files out of commits.
- Include one devlog entry for each meaningful development session.
- Include focused changes and update documentation when behavior changes.
- Use semantic version tags for releases, starting from `v0.1.0-alpha`.
