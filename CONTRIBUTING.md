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

## Git Workflow

- Work from a branch.
- Keep generated files out of commits.
- Include focused changes and update documentation when behavior changes.
- Use semantic version tags for releases, starting from `v0.1.0-alpha`.
