"""Does the engine hold up on companies nobody prepared for?

Runs every code path except the model on a random sample of NSE stocks and reports what broke.
Costs nothing: run with FINN_LLM=none.

    FINN_LLM=none python eval/sweep_tickers.py 60 [SEED]
"""
import json
import random
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta

from finn import nse, pipeline, research

def stay_awake() -> None:
    """A long batch shouldn't stall because the laptop slept. Lasts only as long as this process."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


stay_awake()
n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
# Whoever filed anything recently: a realistic mix, including small companies nobody would prepare for.
now = datetime.now()
pool = sorted({f.symbol for f in nse.announcements(None, now - timedelta(days=7), now, ttl=86400) if f.symbol} - set(pipeline.PORTFOLIO))
if not pool:
    sys.exit("No tickers to sample: a sweep of nothing must not look like a pass.")
random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else 2027)
sample = random.sample(pool, min(n, len(pool)))
print(f"sampling {len(sample)} of {len(pool)} tickers")

tally, failures, gaps = Counter(), [], Counter()
for sym in sample:
    start = time.time()
    try:
        d = research.build(sym, full=False)
        json.dumps(d)  # must be serialisable for the API
        q = d["snapshot"].get("quarter")
        gaps["has filed results"] += bool(q)
        gaps["has trajectory"] += bool(d["trajectory"])
        gaps["has balance sheet"] += bool(d["balance_sheet"])
        gaps["has cash flow"] += bool(d["cash"])
        calls = (d["promises"] or {}).get("calls", [])
        source = (d["promises"] or {}).get("source")
        gaps["has earnings calls"] += source == "earnings_calls"
        gaps["only annual reports"] += source == "annual_reports"
        gaps["neither"] += not calls
        if d["snapshot"].get("banking"):
            gaps["bank format"] += 1
        tally["ok"] += 1
        print(f"ok   {sym:12s} {time.time() - start:4.0f}s calls={len(calls)} q={q['label'] if q else '-'} {d['missing'][:1]}", flush=True)
    except research.UnknownTicker:
        tally["not found"] += 1
        print(f"none {sym}", flush=True)
    except Exception as e:
        tally["crashed"] += 1
        failures.append((sym, traceback.format_exc(limit=3)))
        print(f"FAIL {sym} {type(e).__name__}: {e}", flush=True)

print("\nresult", dict(tally))
print("coverage", dict(gaps))
for sym, tb in failures:
    print(f"\n--- {sym}\n{tb}")

sys.exit(1 if failures else 0)
