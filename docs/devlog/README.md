# SHAI Devlog

Every development session that makes a meaningful project change should create one devlog entry in this folder.

Devlog entries are human-readable patch notes for SHAI. They should explain what changed, why it changed, which files were touched, how the change was tested, and what still needs attention.

Use the helper script before starting a change:

```powershell
python scripts/new_devlog.py
```

The script creates a file named `YYYY-MM-DD-session-NNN.md` and automatically increments the session number for the current date.
