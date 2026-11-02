"""Blind sample for hand labelling: shows the filing, never the classifier's score."""
import json, random, re
from datetime import datetime
from finn import docs, nse
from finn.store import db
rows=[dict(r) for r in db().execute("select id,symbol,ts,raw_category,text,pdf_url from filings where ts>='2026-09-17'")]
random.seed(2027); random.shuffle(rows)
# Importance is rare, so a plain random sample would be nearly all paperwork. Take every label type, capped.
seen={}; pick=[]
for r in rows:
    n=seen.get(r["raw_category"],0)
    if n<9: pick.append(r); seen[r["raw_category"]]=n+1
pick=pick[:170]
out=[]
for i,r in enumerate(pick):
    ex=""
    if r["pdf_url"]:
        try:
            t=docs.load(r["pdf_url"],headers=nse.HEADERS,max_pages=3).text
            m=re.search(r"(?is)(?:\bsub(?:ject)?\b|\bre\b)\s*[:\-–].{0,700}",t)
            ex=re.sub(r"\s+"," ",(m[0] if m else t[300:1000]))[:560]
        except Exception as e: ex=f"[pdf error {type(e).__name__}]"
    out.append({"id":r["id"],"symbol":r["symbol"],"date":r["ts"][:10],"label":r["raw_category"],"feed":(r["text"] or "")[:140],"pdf":ex})
json.dump(out,open("eval/to_label.json","w",encoding="utf-8"),ensure_ascii=False,indent=0)
for i,o in enumerate(out): print(f'{i}|{o["symbol"]}|{o["label"][:30]}|{o["feed"][:90]}|{o["pdf"][:430]}')
