"""screener.in: multi-year financial tables and links to primary documents.

We deliberately ignore its "AI Summary" buttons: those are secondary commentary, and the
pull side is restricted to primary sources. Only the raw transcripts and annual reports are used.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

from .net import FetchError, fetch
from .nse import Quarter

BASE = "https://www.screener.in"


@dataclass
class Table:
    columns: list[str]
    rows: dict[str, list[float | None]]

    def get(self, *names: str) -> list[float | None] | None:
        for name in names:
            for key, vals in self.rows.items():
                if key.lower().rstrip(" +").startswith(name.lower()):
                    return vals
        return None


@dataclass
class Company:
    symbol: str
    name: str
    consolidated: bool
    about: str
    website: str | None
    market_cap: float | None
    price: float | None
    ratios: dict[str, str]
    tables: dict[str, Table]
    transcripts: list[dict] = field(default_factory=list)  # {period, url}
    annual_reports: list[dict] = field(default_factory=list)  # {year, url}
    source_url: str = ""
    warehouse_id: str | None = None


def _num(s: str) -> float | None:
    s = s.replace(",", "").replace("%", "").replace("₹", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _table(section) -> Table | None:
    t = section.find("table") if section else None
    if not t or not t.find("thead"):
        return None
    cols = [th.get_text(strip=True) for th in t.find("thead").find_all("th")][1:]
    rows: dict[str, list[float | None]] = {}
    for tr in t.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).rstrip(" +").strip()
        rows[label] = [_num(c.get_text(strip=True)) for c in cells[1:]]
    return Table(cols, rows)


def _page(symbol: str, consolidated: bool, ttl: float) -> bytes:
    suffix = "consolidated/" if consolidated else ""
    return fetch(f"{BASE}/company/{symbol}/{suffix}", ttl=ttl, retries=2)


def company(symbol: str, ttl: float = 43200) -> Company:
    """Prefer consolidated numbers; fall back to standalone where that page is empty."""
    symbol = symbol.upper().strip()
    html = _page(symbol, True, ttl)
    soup = BeautifulSoup(html, "lxml")
    consolidated = True
    if not _has_data(soup):
        html = _page(symbol, False, ttl)
        soup = BeautifulSoup(html, "lxml")
        consolidated = False
    return _parse(symbol, soup, consolidated)


def _has_data(soup) -> bool:
    t = _table(soup.find("section", id="profit-loss"))
    if not t:
        return False
    sales = t.get("Sales", "Revenue") or []
    return any(v for v in sales)


def _parse(symbol: str, soup, consolidated: bool) -> Company:
    name = (soup.find("h1").get_text(strip=True) if soup.find("h1") else symbol)
    ratios = {}
    for li in soup.select("#top-ratios li"):
        k, v = li.select_one(".name"), li.select_one(".value")
        if k and v:
            ratios[k.get_text(strip=True)] = re.sub(r"\s+", " ", v.get_text(" ", strip=True))
    about = soup.select_one(".company-profile .about p, .company-profile .about")
    site = next((a.get("href") for a in soup.select(".company-links a") if "bseindia" not in a.get("href", "") and "nseindia" not in a.get("href", "")), None)
    tables = {}
    for sec in ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios", "shareholding"):
        t = _table(soup.find("section", id=sec))
        if t:
            tables[sec] = t
    transcripts = []
    for li in soup.select(".documents.concalls li"):
        period = li.find("div")
        link = next((a for a in li.find_all("a") if a.get_text(strip=True) == "Transcript"), None)
        if period and link and link.get("href"):
            transcripts.append({"period": period.get_text(strip=True), "url": link["href"]})
    reports = []
    for a in soup.select(".documents.annual-reports a"):
        m = re.search(r"(20\d\d)", a.get_text(" ", strip=True))
        if m and a.get("href"):
            reports.append({"year": int(m[1]), "url": a["href"]})
    return Company(
        symbol=symbol,
        name=name,
        consolidated=consolidated,
        about=re.sub(r"\s+", " ", about.get_text(" ", strip=True)) if about else "",
        website=site,
        market_cap=_num(ratios.get("Market Cap", "").replace("Cr.", "")),
        price=_num(ratios.get("Current Price", "")),
        ratios=ratios,
        tables=tables,
        transcripts=transcripts,
        annual_reports=sorted(reports, key=lambda r: -r["year"]),
        source_url=f"{BASE}/company/{symbol}/{'consolidated/' if consolidated else ''}",
        warehouse_id=(soup.find(attrs={"data-warehouse-id": True}) or {}).get("data-warehouse-id") if soup.find(attrs={"data-warehouse-id": True}) else None,
    )


def quarters(company: Company, limit: int = 9) -> list[Quarter]:
    """Screener's quarterly table, newest first, for when NSE can't be reached. Rounder and less exact than
    filed XBRL, and its lines differ a little (see docs/RESULTS.md), so anything using them says where they came from."""
    table = company.tables.get("quarters")
    if not table:
        return []
    lender = table.get("Financing Profit") is not None

    def cell(i: int, *names: str) -> float | None:
        values = table.get(*names) or []
        return values[i] if i < len(values) else None

    out = []
    for i, label in enumerate(table.columns):
        try:
            month = datetime.strptime(label, "%b %Y")
        except ValueError:
            continue
        end = f"{month.year}-{month.month:02d}-{calendar.monthrange(month.year, month.month)[1]}"
        q = Quarter(
            symbol=company.symbol, period_end=end, basis="consolidated" if company.consolidated else "standalone", format="banking" if lender else "indas",
            revenue=cell(i, "Sales", "Revenue"), other_income=cell(i, "Other Income"), finance_costs=cell(i, "Interest"), depreciation=cell(i, "Depreciation"),
            pbt=cell(i, "Profit before tax"), pat=cell(i, "Net Profit"), pat_total=cell(i, "Net Profit"), source_url=company.source_url, provider="screener",
        )
        if lender:
            q.nii = round(q.revenue - q.finance_costs, 2) if q.revenue is not None and q.finance_costs is not None else None
            q.gross_npa_pct, q.net_npa_pct = cell(i, "Gross NPA %"), cell(i, "Net NPA %")
        else:
            q.ebitda = cell(i, "Operating Profit")
        out.append(q)
    return list(reversed(out))[:limit]


def peers(company: Company, ttl: float = 86400, limit: int = 6) -> dict | None:
    """Screener's peer table: same industry, same period, its standardised figures."""
    if not company.warehouse_id:
        return None
    try:
        html = fetch(f"{BASE}/api/company/{company.warehouse_id}/peers/", ttl=ttl, headers={"Referer": company.source_url or BASE}, retries=2)
    except FetchError:
        return None
    table = BeautifulSoup(html, "lxml").find("table")
    if not table:
        return None
    head = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)) for th in table.find_all("th")]
    want = {"Mar Cap": "market_cap", "P/E": "pe", "ROCE": "roce", "Qtr Sales Var": "sales_yoy", "Qtr Profit Var": "profit_yoy", "CMP": "price"}
    index = {key: next((i for i, h in enumerate(head) if h.startswith(name)), None) for name, key in want.items()}
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        link = tr.find("a")
        if not link or not cells:
            continue
        m = re.search(r"/company/([^/]+)/", link.get("href", ""))
        row = {"symbol": m[1] if m else None, "name": link.get_text(strip=True), "self": bool(m and m[1].upper() == company.symbol)}
        for key, i in index.items():
            row[key] = _num(cells[i].get_text(strip=True)) if i is not None and i < len(cells) else None
        rows.append(row)
    if len(rows) < 2:
        return None
    own = [r for r in rows if r["self"]]
    return {"rows": (own + [r for r in rows if not r["self"]])[:limit], "source": company.source_url}


__all__ = ["Company", "Table", "company", "peers", "quarters", "FetchError"]
