"""Build companies on Groq's free tier, on a copy of the database, and print every verdict with its evidence to read.

Pick companies nothing has been tuned on. It uses up the day's free allowance quickly: about 40,000 to 110,000 tokens a company.

    uv run python eval/free_tier_audit.py VOLTAS BLUESTARCO
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
copy = Path(tempfile.mkdtemp(prefix="finn-free-audit-")) / "finn.db"
shutil.copy(ROOT / "data" / "finn.db", copy)
os.environ["FINN_DB"] = str(copy)
sys.path.insert(0, str(ROOT))

from finn import llm, research  # noqa: E402
from finn.store import db  # noqa: E402

if not llm.frugal():
    sys.exit("This audits the free tier. Put only GROQ_API_KEY in finn/.env.")


def used() -> int:
    return db().execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) t FROM llm_calls WHERE provider='groq'").fetchone()["t"]


for symbol in sys.argv[1:]:
    db().execute("DELETE FROM dossiers WHERE symbol=?", (symbol,))
    db().commit()
    start, before = time.time(), used()
    d = research.build(symbol, log=lambda step: print(f"[{time.time() - start:5.0f}s] {step}", flush=True))
    pr = d.get("promises") or {}
    print(f"\n{symbol}: {(time.time() - start) / 60:.1f} minutes, {used() - before:,} tokens, {pr.get('tally')}")
    for note in d.get("missing") or []:
        print(f"  ! {note}")
    for p in pr.get("promises", []):
        if p["verdict"] in ("delivered", "partly", "missed"):
            e = p.get("evidence") or {}
            print(f"\n[{p['verdict']}, {p.get('checked_by') or 'model'}] {p['claim']} (due {p.get('period')}, said {p['call_label']})")
            print(f"  Promise: {p['quote']}\n  Note: {p.get('note')}")
            if e:
                print(f"  Evidence, {e['call_label']}: {e['quote']}")
