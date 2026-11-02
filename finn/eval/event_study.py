"""Which filings actually move stocks? Measures the ranking instead of assuming it.

For every filing on NSE over the study period: did the stock move unusually on the first session
that could react, compared with its own recent behaviour and the market that day? And did volume
jump? Each filing type is compared with the same stocks on days they filed nothing.

Only outcomes are kept. Prices and filings sit in the disposable cache; what ships is one small
table of evidence per filing type, finn/calibration.json.

    python eval/event_study.py fetch    # filings and prices, slow the first time, then cached
    python eval/event_study.py report   # measure, hold out the latest months, write calibration.json

Limits: a price move is evidence that investors cared, not proof a filing caused it. Things that
matter but aren't priced at once (a governance problem) score low here, so rules still protect them.
"""
import bisect
import gzip
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from finn import classify as C
from finn import enrich as E
from finn import nse, prices
from finn.net import CACHE_DIR, FetchError, fetch_json

WEEKS = 78
HOLDOUT_FROM_WEEKS_AGO = 26
BIG = 2.0  # a "big move": over 2 of the stock's own recent standard deviations, after the market's move
MIN_N = 40
HISTORY = CACHE_DIR / "event_filings.json.gz"
PRICES = CACHE_DIR / "event_prices.json.gz"
OUT = Path(__file__).resolve().parent.parent / "finn" / "calibration.json"


def fetch() -> None:
    now = datetime.now()
    seen, rows = set(), []
    for w in range(WEEKS):
        to = now - timedelta(days=7 * w)
        try:
            week = nse.announcements(None, to - timedelta(days=7), to, ttl=None if w else 3600)
        except (FetchError, ValueError) as e:
            print(f"week {w}: {e}", flush=True)
            continue
        for f in week:
            if f.id not in seen and f.symbol:
                seen.add(f.id)
                v = C.rules(f.raw_category, f.text)
                # Everything needed later, and nothing else: no text, no PDF links.
                rows.append((f.symbol, f.ts, v.category, v.reason, v.score, E.amount_crore_rule(f.text)))
        print(f"week {w}: {len(rows):,} filings", flush=True)
    with gzip.open(HISTORY, "wt", encoding="utf-8") as fh:
        json.dump(rows, fh)
    symbols = sorted({r[0] for r in rows})
    print(f"{len(rows):,} filings, {len(symbols):,} companies", flush=True)
    series = {}
    for i, sym in enumerate(symbols + ["^NSEI"]):
        try:
            if sym == "^NSEI":
                r = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=2y&interval=1d", ttl=86400)["chart"]["result"][0]
                closes = r["indicators"]["quote"][0]["close"]
                hist = [{"date": datetime.fromtimestamp(t, prices.IST).date().isoformat(), "close": c, "volume": 1.0} for t, c in zip(r["timestamp"], closes) if c]
            else:
                hist = prices.history(sym, "2y", ttl=86400)
        except Exception:
            continue
        if len(hist) > 100:
            series[sym] = [[h["date"], h["close"], h["volume"]] for h in hist]
        if i % 100 == 0:
            print(f"prices {i}/{len(symbols)}: {len(series)} usable", flush=True)
    with gzip.open(PRICES, "wt", encoding="utf-8") as fh:
        json.dump(series, fh)
    print(f"prices for {len(series)} companies", flush=True)


def daily_stats(rows: list, market: dict[str, float]) -> dict[str, tuple]:
    """Per session: market-adjusted move in units of the stock's own recent volatility, and volume
    against its recent median. Both baselines stop the day before, so a spike can't hide itself."""
    out = {}
    adj = []
    for i in range(1, len(rows)):
        d, c, v = rows[i]
        ret = c / rows[i - 1][1] - 1
        m = market.get(d)
        if m is None:
            continue
        a = ret - m
        if len(adj) >= 40:
            sd = st.pstdev(adj[-60:])
            base = st.median(r[2] for r in rows[max(0, i - 20):i])
            if sd > 0 and base > 0:
                out[d] = (a / sd, v / base, a)
        adj.append(a)
    return out


def load():
    with gzip.open(HISTORY, "rt", encoding="utf-8") as fh:
        filings = json.load(fh)
    with gzip.open(PRICES, "rt", encoding="utf-8") as fh:
        series = json.load(fh)
    return filings, series


def wilson(k: int, n: int) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d, (c + m) / d


def summarise(events: list[tuple], base_rate: float, base_vol: float) -> dict:
    n = len(events)
    big = sum(abs(e[0]) >= BIG for e in events)
    low, high = wilson(big, n)
    return {
        "n": n,
        "big_move_pct": round(big / n * 100, 1),
        "lift": round(big / n / base_rate, 2) if base_rate else None,
        "lift_low": round(low / base_rate, 2) if base_rate else None,
        "lift_high": round(high / base_rate, 2) if base_rate else None,
        "median_move_pct": round(st.median(abs(e[2]) for e in events) * 100, 2),
        "median_volume_x": round(st.median(e[1] for e in events), 2),
        "volume_vs_normal": round(st.median(e[1] for e in events) / base_vol, 2) if base_vol else None,
    }


# A move and its price query arrive together, but the query is caused by the move. Measuring it says nothing.
REACTIVE = {"Exchange asked the company to respond to a market report", "Exchange queried unusual trading volume", "Price movement"}
# Types an investor must see whatever the immediate price did. The brief names senior changes as material, and some
# problems are priced slowly or have too few cases to measure. Each is disclosed, with its evidence beside it.
FLOORS = {
    "A top executive is leaving": (72, "Named as material in the brief"), "A new top executive": (72, "Named as material in the brief"),
    "Defaults on Payment of Interest/Principal": (72, "Too few cases to measure, and serious if true"),
    "Trading suspension notice": (80, "Too few cases to measure, and serious if true"),
    "Insolvency or tribunal proceedings": (40, "Most are minor creditor petitions, so the PDF decides. Admission ranks top at any size"),
    "Regulatory or tax action; importance depends on the amount": (40, "The amount decides, once the PDF is read"),
    "Deal: importance depends on its size against revenue": (40, "The size decides, once the PDF is read"),
    "Fresh equity raise, which dilutes holders": (55, "Dilution is permanent even when the day's move is modest"),
    "Promoter changed the pledge on their shares": (55, "Too few cases to measure precisely, and a known risk signal"),
    "A large holder crossed a disclosure threshold": (45, "Too few cases to measure precisely"),
    "Buyback: the company returning cash, not raising it": (45, "Too few cases to measure precisely"),
    "Closure of operations": (60, "Too few cases to measure, and serious if true"),
    "Strikes/Lockouts/Disturbances": (45, "Too few cases to measure, and serious if true"),
    "New capacity or capital spending": (40, "The size decides, once the PDF is read"),
}
# The reaction belongs to something else that happened nearby, so it isn't evidence about the document.
BORROWED = {
    "Earnings call recording link": "The call moved the stock, not the link to its recording",
    "Investor presentation": "Usually filed around results or a call, which is what moved the stock",
}
# Measured high, but the study can't fully separate these from a results day, or they fall on board-meeting days.
CAPS = {
    "Dividend declared or recommended": (65, "Often decided alongside results, so part of this reaction is theirs"),
    "Auditor or secretarial appointment": (30, "Tends to fall on board-meeting days, and the filing itself is paperwork"),
}
# How often a type moves the stock sharply, relative to a quiet day, turned into a rank.
SCALE = [(1.0, 5), (1.25, 20), (1.5, 34), (1.6, 40), (2.0, 50), (2.5, 58), (3.2, 70), (4.0, 76), (5.0, 85), (7.0, 90)]


def to_score(lift: float) -> int:
    if lift <= SCALE[0][0]:
        return SCALE[0][1]
    for (a, sa), (b, sb) in zip(SCALE, SCALE[1:]):
        if lift <= b:
            return round(sa + (sb - sa) * (lift - a) / (b - a))
    return SCALE[-1][1]


def decide(reason: str, s: dict, rule: int) -> tuple[int, str]:
    if reason == "New quarterly results filed":
        return max(to_score(s["lift"]), 85), "measured"
    if reason in REACTIVE:
        return max(rule, 55), "kept: the move causes this filing, not the other way round"
    if reason in BORROWED:
        return rule, f"kept: {BORROWED[reason]}"
    # With few cases the range is wide, so rank between its cautious end and its middle.
    # Few cases aren't evidence of no effect, so they can't bury a filing on their own.
    wide = s["lift_high"] - s["lift_low"] > 1.0
    score = to_score((s["lift_low"] + s["lift"]) / 2 if wide else s["lift"])
    if reason in FLOORS and score < FLOORS[reason][0]:
        return FLOORS[reason][0], f"floor: {FLOORS[reason][1]}"
    if reason in CAPS and score > CAPS[reason][0]:
        return CAPS[reason][0], f"cap: {CAPS[reason][1]}"
    return score, "measured, cautiously: few cases" if wide else "measured"


def tier_table(events: list[tuple], score_of, label: str, base_rate: float) -> dict:
    tiers = defaultdict(list)
    for e in events:
        tiers[C.tier(score_of(e))].append(e)
    print(f"  {label}")
    out = {}
    for t in ("top", "notable", "routine"):
        x = tiers[t]
        if not x:
            continue
        big = sum(abs(v[0]) >= BIG for v in x) / len(x)
        out[t] = {"n": len(x), "big_move_pct": round(big * 100, 1), "lift": round(big / base_rate, 2)}
        print(f"    {t:8s} {len(x):7,} filings ({len(x) / len(events) * 100:4.1f}%)  big move {big * 100:5.1f}%  = {big / base_rate:4.2f}x a quiet day")
    return out


def report() -> None:
    filings, series = load()
    market = {}
    idx = series.pop("^NSEI")
    for a, b in zip(idx, idx[1:]):
        market[b[0]] = b[1] / a[1] - 1
    sessions = sorted(market)
    stats = {}
    for sym, rows in series.items():
        stats[sym] = daily_stats(rows, market)
    print(f"{len(filings):,} filings, {len(stats):,} companies with prices, {len(sessions)} sessions")

    def reaction_day_fast(ts: str) -> str | None:
        """The first session that could react: after 3:30 pm, or on a holiday, that's the next one."""
        t = datetime.fromisoformat(ts)
        d = t.date() + timedelta(days=1) if (t.hour, t.minute) >= (15, 30) else t.date()
        i = bisect.bisect_left(sessions, d.isoformat())
        return sessions[i] if i < len(sessions) else None

    by_day = defaultdict(set)
    days_with_results = set()
    tagged = []
    for sym, ts, cat, reason, score, amount in filings:
        if sym not in stats:
            continue
        d = reaction_day_fast(ts)
        if d is None or d not in stats[sym]:
            continue
        by_day[(sym, d)].add((cat, reason, score))
        tagged.append((sym, d, cat, reason, score, amount))
        if cat == "results":
            days_with_results.add((sym, d))

    # What a stock does on days with nothing filed is the yardstick for everything else.
    quiet = [v for sym, days in stats.items() for d, v in days.items() if (sym, d) not in by_day]
    base_rate = sum(abs(v[0]) >= BIG for v in quiet) / len(quiet)
    base_vol = st.median(v[1] for v in quiet)
    print(f"quiet days: {len(quiet):,}; a big move on {base_rate * 100:.1f}% of them; median volume {base_vol:.2f}x")

    cutoff = (datetime.now() - timedelta(weeks=HOLDOUT_FROM_WEEKS_AGO)).date().isoformat()
    groups = defaultdict(lambda: {"train": [], "test": [], "scores": Counter(), "cat": None})
    seen = set()
    position = {d: i for i, d in enumerate(sessions)}
    # Size of a company, roughly: what trades in a day. A third of them are "large", like most of the portfolio.
    traded = {sym: st.median(r[1] * r[2] for r in rows[-120:]) for sym, rows in series.items()}
    cut = sorted(traded.values())[len(traded) * 2 // 3]
    large = {sym for sym, v in traded.items() if v >= cut}
    test_events = []
    large_groups = defaultdict(list)
    sized = defaultdict(list)
    for sym, d, cat, reason, score, amount in tagged:
        key = (sym, d, reason)
        if key in seen:
            continue
        seen.add(key)
        # A deck or a recording filed the day after results would borrow the results' reaction.
        i = position[d]
        near = [sessions[j] for j in (i - 1, i, i + 1) if 0 <= j < len(sessions)]
        if cat != "results" and any((sym, x) in days_with_results for x in near):
            continue
        if sym in large:
            large_groups[reason].append(stats[sym][d])
        if amount and reason in ("Order win: importance depends on its size against revenue", "Deal: importance depends on its size against revenue") and traded.get(sym):
            # Against a year's trading value, as a stand-in for size that needs no filing to be read.
            sized[reason].append((amount * 1e7 / (traded[sym] * 250), stats[sym][d]))
        if d >= cutoff:
            test_events.append((*stats[sym][d], reason, score))
        g = groups[reason]
        g["cat"] = cat
        g["scores"][score] += 1
        g["train" if d < cutoff else "test"].append(stats[sym][d])

    rows = []
    for reason, g in groups.items():
        everything = g["train"] + g["test"]
        if len(everything) < MIN_N:
            continue
        s = summarise(everything, base_rate, base_vol)
        rule = g["scores"].most_common(1)[0][0]
        s.update(reason=reason, category=g["cat"], rule_score=rule)
        s["train_lift"] = summarise(g["train"], base_rate, base_vol)["lift"] if len(g["train"]) >= MIN_N else None
        s["test_lift"] = summarise(g["test"], base_rate, base_vol)["lift"] if len(g["test"]) >= MIN_N else None
        s["score"], s["decision"] = decide(reason, s, rule)
        big = large_groups.get(reason, [])
        s["large_cap_lift"] = summarise(big, base_rate, base_vol)["lift"] if len(big) >= MIN_N else None
        rows.append(s)
    rows.sort(key=lambda r: -(r["lift"] or 0))
    print(f"\n{'lift':>5} {'95% CI':>11} {'big%':>5} {'vol×':>5} {'n':>7} {'large':>5} {'rule':>4} {'new':>4}  train/test  filing type")
    for r in rows:
        mark = "*" if C.tier(r["rule_score"]) != C.tier(r["score"]) else " "
        print(f"{r['lift']:5.2f} {r['lift_low']:5.2f}-{r['lift_high']:5.2f} {r['big_move_pct']:5.1f} {r['volume_vs_normal']:5.2f} {r['n']:7,} {str(r['large_cap_lift']):>5} {r['rule_score']:4d} {r['score']:4d}{mark} {str(r['train_lift']):>5}/{str(r['test_lift']):<5} {r['reason'][:52]} | {r['decision'][:22]}")
    print("\nDoes size matter? Reaction by the amount in the filing, against a year's trading value")
    for reason, items in sized.items():
        items.sort(key=lambda x: x[0])
        third = len(items) // 3
        for name, part in (("small", items[:third]), ("middle", items[third:2 * third]), ("large", items[2 * third:])):
            if part:
                x = [p[1] for p in part]
                print(f"  {reason[:12]:12s} {name:7s} n={len(x):5,}  big move {sum(abs(v[0]) >= BIG for v in x) / len(x) * 100:5.1f}%  median volume {st.median(v[1] for v in x):.2f}x")
    # The honest test: ranks learned from the first year only, judged on the six months they never saw.
    trained = {}
    for reason, g in groups.items():
        if len(g["train"]) >= MIN_N:
            t = summarise(g["train"], base_rate, base_vol)
            trained[reason] = decide(reason, t, g["scores"].most_common(1)[0][0])[0]
    print(f"\nHold-out: the last {HOLDOUT_FROM_WEEKS_AGO} weeks, {len(test_events):,} filings, ranks learned without them")
    old = tier_table(test_events, lambda e: e[4], "Old hand-set rules", base_rate)
    new = tier_table(test_events, lambda e: trained.get(e[3], e[4]), "Measured ranks", base_rate)
    no_results = [e for e in test_events if e[3] != "New quarterly results filed"]
    print("  Leaving out results, the easy case")
    old_x = tier_table(no_results, lambda e: e[4], "Old hand-set rules", base_rate)
    new_x = tier_table(no_results, lambda e: trained.get(e[3], e[4]), "Measured ranks", base_rate)
    if len(rows) > 2:
        a, b = [r["rule_score"] for r in rows], [r["lift"] for r in rows]
        print(f"\nSpearman, rule score against measured lift: {spearman(a, b):.2f} across {len(rows)} filing types")
    OUT.write_text(json.dumps({
        "generated": datetime.now().date().isoformat(), "filings": len(filings), "companies": len(stats),
        "big_move_days_quiet_pct": round(base_rate * 100, 2), "weeks": WEEKS, "types": rows,
        "holdout": {"old": old, "new": new, "old_without_results": old_x, "new_without_results": new_x},
    }, indent=0, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.name}: {OUT.stat().st_size / 1024:.0f} KB")


def spearman(a: list, b: list) -> float:
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    top = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return top / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


if __name__ == "__main__":
    {"fetch": fetch, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
