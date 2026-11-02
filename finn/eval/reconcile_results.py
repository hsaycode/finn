"""Do our XBRL-parsed results match an independent source?

Compares each quarter's Revenue, PBT and PAT against screener.in's table for the whole portfolio.
Mismatches are printed rather than hidden. Exact agreement isn't always expected: screener can
restate history, and its "Net Profit" may be before minority interest.

    python eval/reconcile_results.py
"""
from collections import Counter

from finn import nse, pipeline, screener

MONTHS = {"Mar": "03-31", "Jun": "06-30", "Sep": "09-30", "Dec": "12-31"}


def close(a, b, tol=0.015):
    if a is None or b is None:
        return None
    if abs(a - b) <= 2:  # rounding to the crore on small numbers
        return True
    return abs(a - b) <= tol * max(abs(a), abs(b))


tally, misses = Counter(), []
for sym in pipeline.PORTFOLIO:
    c = screener.company(sym)
    t = c.tables.get("quarters")
    if not t:
        print(sym, "no screener quarters")
        continue
    table = {}
    for i, col in enumerate(t.columns):
        mon, yr = col.split()
        table[f"{yr}-{MONTHS[mon]}"] = i
    for q in nse.quarters(sym):
        i = table.get(q.period_end)
        if i is None:
            continue
        top = (t.get("Sales", "Revenue") or [None] * 99)[i]
        interest = (t.get("Interest") or [None] * 99)[i]
        rows = {
            # Banks are compared on net interest income, which is what their card shows.
            "nii" if q.format in ("banking", "nbfc") else "revenue": (top - interest) if q.format in ("banking", "nbfc") and top is not None and interest is not None else top,
            "pbt": (t.get("Profit before tax") or [None] * 99)[i],
            "pat_total": (t.get("Net Profit") or [None] * 99)[i],
        }
        for field, theirs in rows.items():
            ours = getattr(q, field)
            ok = close(ours, theirs)
            if ok is None:
                tally["not comparable"] += 1
            elif ok:
                tally["match"] += 1
            else:
                tally["mismatch"] += 1
                misses.append(f"{sym:11s} {q.period_end} {field:8s} ours {ours:>12,.1f}  screener {theirs:>12,.1f}  {q.format}")

total = tally["match"] + tally["mismatch"]
print(f"\ncompared {total} figures across {len(pipeline.PORTFOLIO)} companies: {tally['match']} match ({tally['match'] / total * 100:.1f}%), {tally['mismatch']} differ, {tally['not comparable']} not comparable")
for m in misses:
    print(" ", m)
