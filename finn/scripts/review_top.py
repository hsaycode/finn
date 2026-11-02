"""Print what the classifier currently considers important, for eyeballing after a change."""
import json
import sys

from finn.store import db

c = db()
print(dict(c.execute("select count(*) n, sum(cost_usd) usd from llm_calls").fetchone()))
tiers = sys.argv[1:] or ["top"]
for t in tiers:
    print(f"\n== {t}")
    q = "select substr(ts,1,10) d,symbol,score,category,title,reason,detail from filings where enriched=2 and tier=? order by score desc, ts desc"
    for r in c.execute(q, (t,)):
        d = json.loads(r["detail"])
        print(r["d"][5:], r["symbol"], r["score"], r["category"][:8], (d.get("tone") or "")[:3], "|", r["title"][:84], "|", r["reason"][:44], "|", d.get("amount_crore"))
print([tuple(r) for r in c.execute("select tier,count(*) from filings where ts>='2026-09-17' group by 1")])
