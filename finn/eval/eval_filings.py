"""Score the filing ranker against labelled filings.

    python eval/eval_filings.py dev       # tune against this
    python eval/eval_filings.py holdout   # report this; don't tune against it

Labels were assigned by reading each filing without seeing its score. T=top, N=notable, R=routine.
"""
import json
import sys
from collections import Counter
from pathlib import Path

from finn.store import db

HERE = Path(__file__).parent
LABELS = (
    "T R R R N R T N N R N R R T N N R N T R R R R R N R T R R N N R R T N T R R N R R R R R N R R N T R "
    "R R R R N N T R R R T T R R N R R R R R R N T N T R R R R N R N R R"
).split()

items = json.load(open(HERE / "to_label.json", encoding="utf-8"))[: len(LABELS)]
split = sys.argv[1] if len(sys.argv) > 1 else "dev"
rows = []
for i, (item, label) in enumerate(zip(items, LABELS)):
    if (i % 2 == 0) != (split == "dev"):
        continue
    r = db().execute("select tier, score, title, reason from filings where id=?", (item["id"],)).fetchone()
    rows.append({**item, "truth": {"T": "top", "N": "notable", "R": "routine"}[label], "pred": r["tier"], "score": r["score"], "title": r["title"], "reason": r["reason"]})

n = len(rows)
tops = [r for r in rows if r["truth"] == "top"]
routine = [r for r in rows if r["truth"] == "routine"]
pred_top = [r for r in rows if r["pred"] == "top"]
print(f"{split}: {n} filings  truth={dict(Counter(r['truth'] for r in rows))}  pred={dict(Counter(r['pred'] for r in rows))}")
print(f"exact tier match:             {sum(r['truth'] == r['pred'] for r in rows)}/{n}")
print(f"important not buried (T>=N):  {sum(r['pred'] != 'routine' for r in tops)}/{len(tops)}")
print(f"important ranked top:         {sum(r['pred'] == 'top' for r in tops)}/{len(tops)}")
print(f"top that deserved attention:  {sum(r['truth'] != 'routine' for r in pred_top)}/{len(pred_top)}")
print(f"paperwork correctly buried:   {sum(r['pred'] == 'routine' for r in routine)}/{len(routine)}")
if split == "dev" or "-v" in sys.argv:
    print("\nmismatches:")
    for r in rows:
        if r["truth"] != r["pred"]:
            print(f"  want {r['truth']:7s} got {r['pred']:7s} {r['score']:3d} {r['symbol']:10s} {r['title'][:70]} | {r['reason'][:40]}")
