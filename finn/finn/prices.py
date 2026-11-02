"""End-of-day prices from Yahoo's chart endpoint (the same data yfinance wraps)."""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from .net import FetchError, fetch_json

IST = timezone(timedelta(hours=5, minutes=30))


def history(symbol: str, range_: str = "1y", ttl: float = 3600) -> list[dict]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}.NS?range={range_}&interval=1d"
    r = fetch_json(url, ttl=ttl)["chart"]["result"][0]
    stamps = r.get("timestamp") or []
    quote = r["indicators"]["quote"][0]
    out = []
    for ts, close, vol in zip(stamps, quote["close"], quote["volume"]):
        if close is None or not vol:
            continue
        out.append({
            "date": datetime.fromtimestamp(ts, IST).date().isoformat(),
            "close": round(close, 2),
            "volume": float(vol),
        })
    return out


def quote(symbol: str, ttl: float = 60) -> dict | None:
    """The latest traded price Yahoo has, and today's path. It can lag the exchange, so it carries its own time."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}.NS?range=1d&interval=5m"
    try:
        r = fetch_json(url, ttl=ttl, retries=2)["chart"]["result"][0]
    except (FetchError, KeyError, IndexError, TypeError, ValueError):
        return None
    meta = r.get("meta") or {}
    price, prev = meta.get("regularMarketPrice"), meta.get("chartPreviousClose") or meta.get("previousClose")
    stamp = meta.get("regularMarketTime")
    if price is None or not stamp:
        return None
    closes = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    stamps = r.get("timestamp") or []
    today = [(t, c) for t, c in zip(stamps, closes) if c is not None]
    return {
        "symbol": symbol, "price": round(price, 2), "previous_close": prev,
        "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
        "high": meta.get("regularMarketDayHigh"), "low": meta.get("regularMarketDayLow"), "volume": meta.get("regularMarketVolume"),
        "high_52w": meta.get("fiftyTwoWeekHigh"), "low_52w": meta.get("fiftyTwoWeekLow"),
        "as_of": datetime.fromtimestamp(stamp, IST).strftime("%-d %b, %H:%M IST" if os.name != "nt" else "%#d %b, %H:%M IST"),
        "today": [round(c, 2) for _, c in today],
        "today_times": [datetime.fromtimestamp(t, IST).strftime("%H:%M") for t, _ in today],
    }


def signals(rows: list[dict], window: int = 20) -> list[dict]:
    """Day move and volume multiple per session. The baseline excludes the day itself,
    otherwise a huge spike would inflate the average it's being compared with."""
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            continue
        base = rows[max(0, i - window):i]
        avg = sum(b["volume"] for b in base) / len(base)
        out.append({
            **r,
            "move_pct": round((r["close"] / rows[i - 1]["close"] - 1) * 100, 2),
            "vol_x": round(r["volume"] / avg, 2) if avg else None,
            "enough_history": len(base) >= window,
        })
    return out
