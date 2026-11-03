"""Refresh the data from this computer, where NSE answers, and make it ready to ship with the app.

    uv run python scripts/publish_data.py          # refresh, then print what to commit
    uv run python scripts/publish_data.py --push   # refresh, commit the database and push it

Pushing the database makes Render redeploy, and the app then wakes with these filings as its starting point.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finn.pipeline import refresh, thread_events
from finn.store import DB_PATH, db

summary = refresh()
thread_events()
# The app ships the database file alone, so anything still in the write-ahead log would be lost.
db().execute("PRAGMA wal_checkpoint(TRUNCATE)")
db().commit()
print(json.dumps(summary, indent=1))
latest = db().execute("SELECT MAX(ts) m FROM filings").fetchone()["m"]
print(f"Filings to {latest}. Companies not reached: {summary['failed'] or 'none'}.")
if summary["failed"]:
    sys.exit("Not publishing: some filings couldn't be checked.")

path = str(DB_PATH)
if "--push" in sys.argv:
    subprocess.run(["git", "add", path], check=True)
    subprocess.run(["git", "commit", "-m", f"Refresh data to {latest[:10]}"], check=True)
    subprocess.run(["git", "push"], check=True)
else:
    print(f'Now run:\n  git add "{path}"\n  git commit -m "Refresh data to {latest[:10]}"\n  git push')
