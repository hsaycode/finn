"""Pre-build research so the demo set opens instantly. Safe to re-run: model answers are cached.

    FINN_LLM=claude-cli python scripts/build_dossiers.py [SYMBOL ...]
"""
import json
import sys
import time

from finn import llm, pipeline, research

# Beyond the portfolio: an NBFC, an airline, a consumer staple, a small cap and a new listing's worth of edge cases.
AD_HOC = ["BAJFINANCE", "INDIGO", "NESTLEIND", "TITAGARH", "CDSL"]

def stay_awake() -> None:
    """A long batch shouldn't stall because the laptop slept. Lasts only as long as this process."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


stay_awake()
symbols = sys.argv[1:] or pipeline.PORTFOLIO + AD_HOC
for sym in symbols:
    start, before = time.time(), llm.spent_usd()
    try:
        d = research.build(sym)
        research.save(sym, d)
        pr = d.get("promises") or {}
        print(
            f"{sym:11s} {time.time() - start:5.0f}s ${llm.spent_usd() - before:.3f} calls={len(pr.get('calls', []))} "
            f"tally={json.dumps(pr.get('tally', {}))} missing={d['missing']}",
            flush=True,
        )
    except Exception as e:  # keep going: one bad company shouldn't stop the rest
        print(f"{sym:11s} FAILED {type(e).__name__}: {str(e)[:160]}", flush=True)
print(f"total spent so far ${llm.spent_usd():.3f}")
