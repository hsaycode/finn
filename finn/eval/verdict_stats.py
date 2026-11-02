"""What the second-opinion pass did, across every dossier. Prints each surviving verdict for auditing."""
import sys
from collections import Counter

from finn.store import db
from finn import research

show = "-v" in sys.argv
totals, overturned, by_code = Counter(), Counter(), Counter()
for (sym,) in db().execute("SELECT symbol FROM dossiers ORDER BY symbol").fetchall():
    d = research.load(sym)
    pr = d.get("promises")
    if not pr:
        continue
    for p in pr["promises"]:
        totals[p["verdict"]] += 1
        if p.get("overturned"):
            overturned[p["overturned"]["verdict"]] += 1
        if p["verdict"] in ("delivered", "partly", "missed"):
            by_code["code" if p.get("checked_by") == "code" else "model"] += 1
            if show:
                print(f"{sym:10s} [{p['verdict']:9s}] {p['claim'][:72]} | due {p.get('period')} | {p.get('actual') or ''} {p['note'][:95]}")
print("\nverdicts", dict(totals))
print("settled verdicts by", dict(by_code))
print("overturned by review", dict(overturned), "of", sum(overturned.values()) + sum(by_code.values()) - by_code["code"], "model-made verdicts reviewed")
