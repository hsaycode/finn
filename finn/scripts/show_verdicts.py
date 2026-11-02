"""Print a dossier's checkable verdicts with their sources, for auditing by eye."""
import sys

from finn import research

for sym in sys.argv[1:]:
    d = research.load(sym)
    if not d or not d.get("promises"):
        print(sym, "no dossier")
        continue
    pr = d["promises"]
    print(f"\n===== {sym} {d['name']} tally={pr['tally']} calls={[c['label'] for c in pr['calls']]}")
    for m in pr["moves"]:
        print(f"  MOVE {m['metric']} {m['period']}: {m['from']} -> {m['to']} {m['unit']} ({m['from_call']} -> {m['to_call']})\n     from: {m['from_quote'][:160]}\n     to:   {m['to_quote'][:160]}")
    for p in pr["promises"]:
        if p["verdict"] in ("missed", "partly", "delivered"):
            print(f"\n[{p['verdict']}] {p['claim']} | {p['metric']} {p['low']} {p['unit']} due {p['period']} basis={p.get('basis')} said={p['call_label']} p{p['page']} {p.get('actual') or ''} by={p.get('checked_by', 'model')}")
            print(f"   said: {p['quote'][:230]}")
            print(f"   note: {p['note']}")
            if p.get("evidence"):
                print(f"   then: ({p['evidence']['call_label']} p{p['evidence']['page']}) {p['evidence']['quote'][:230]}")
