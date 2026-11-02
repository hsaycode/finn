"""Does the expensive model earn its price on judging promises?

Runs the same evidence through Sonnet and Opus and prints where they disagree, plus what each cost.
A separate cache salt keeps the shipped answers untouched.

    FINN_LLM=claude-cli python eval/bakeoff_judge.py KAYNES DIXON IDEA
"""
import os
import sys

from finn import llm, nse, promises as P, research, screener
from finn import transcripts as T

symbols = sys.argv[1:] or ["KAYNES", "DIXON", "IDEA"]
results, costs = {}, {}
for model in ("sonnet", "opus"):
    llm._CLI[llm.STRONG] = model
    for sym in symbols:
        c = screener.company(sym)
        qs = nse.quarters(sym)
        calls = research.load_calls(c)
        os.environ["FINN_CACHE_SALT"] = ""
        terms = T.mask_terms(c.name)
        raw = []
        for call in calls:
            raw += P.extract(call, terms)  # shared cheap step: same input for both models
        os.environ["FINN_CACHE_SALT"] = f"bakeoff-{model}"
        promises = P.dedupe(raw)
        latest = max(q.period_end for q in qs)
        need = [p for p in promises if p["material"] and p["kind"] != "vague" and not (p["kind"] == "financial" and p["metric"] != "other" and P.parse_period(p.get("period")))]
        # Only promises already due: open ones have nothing to judge.
        need = [p for p in need if not (P.parse_period(p.get("period")) and P.parse_period(p["period"])["end"] > latest)]
        before = llm.spent_usd()
        P.judge_operational(need, calls, latest, terms)
        costs[(model, sym)] = llm.spent_usd() - before
        for p in need:
            results.setdefault((sym, p["claim"]), {})[model] = (p.get("verdict"), p.get("note"))
        os.environ["FINN_CACHE_SALT"] = ""

agree = disagree = 0
for (sym, claim), r in results.items():
    a, b = r.get("sonnet", (None,))[0], r.get("opus", (None,))[0]
    if a == b:
        agree += 1
    else:
        disagree += 1
        print(f"\n{sym}: {claim}\n  sonnet {a}: {r['sonnet'][1]}\n  opus   {b}: {r['opus'][1]}")
print(f"\nagree {agree}, disagree {disagree}")
for k, v in costs.items():
    print(k, f"${v:.3f}")
