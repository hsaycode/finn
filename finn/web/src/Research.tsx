import { useEffect, useRef, useState } from "react";
import { CompareSection } from "./Compare";
import { METRIC_NAMES, VERDICT, api, cr, fromSource, load, marketMoment, save, signed, type Metric, type Pin, type Promise_, type Verdict } from "./lib";
import { Bars, Collapse, PriceChart, SourceLink, VerdictTag } from "./ui";

/* The dossier is a large, source-shaped document and each section tolerates missing pieces. */
type Dossier = any;

const said = (p: Promise_) => Array.from(new Set([...(p.restated ?? []), ...(p.reaffirmed ?? [])]));

const pagesLabel = (q: { page: number; pages?: number[] }) => (q.pages && q.pages.length > 1 ? `pages ${q.pages.join(", ")}` : `page ${q.page}`);

function Receipts({ p }: { p: Promise_ }) {
  return (
    <div className="receipts">
      <figure className="quote">
        <blockquote>{p.quote}</blockquote>
        <figcaption>
          {p.speaker || "Management"}, {fromSource(p.call_label)} · <SourceLink url={p.url}>{pagesLabel(p)}</SourceLink>
        </figcaption>
      </figure>
      {p.deadline_moves && <p className="slip num">Deadline moved: {p.deadline_moves.join(" → ")}</p>}
      {said(p).length > 0 && <p className="meta">Repeated on the {said(p).join(", ")} {said(p).length > 1 ? "calls" : "call"}.</p>}
      {p.basis && <p className="meta">Stated basis: {p.basis}. Filed results are reported figures, so compare with care.</p>}
      {p.note && <p className="outcome">{p.note}{p.checked_by === "code" ? " Calculated from the filed XBRL, not by a model." : ""}</p>}
      {p.evidence && (
        <figure className="quote quote-later">
          <blockquote>{p.evidence.quote}</blockquote>
          <figcaption>
            {p.evidence.speaker || "Management"}, {fromSource(p.evidence.call_label)} · <SourceLink url={p.evidence.url}>{pagesLabel(p.evidence)}</SourceLink>
          </figcaption>
        </figure>
      )}
    </div>
  );
}

function PromiseRow({ p, symbol }: { p: Promise_; symbol: string }) {
  const [pins, setPins] = useState<Pin[]>(() => load("finn.pins", []));
  const pinned = pins.some((x) => x.symbol === symbol && x.claim === p.claim);
  const toggle = () => {
    const next = pinned
      ? pins.filter((x) => !(x.symbol === symbol && x.claim === p.claim))
      : [...pins, { symbol, claim: p.claim, period: p.period, call_label: p.call_label, url: p.url, page: p.page, quote: p.quote }];
    setPins(next);
    save("finn.pins", next);
  };
  return (
    <Collapse
      className="promise"
      summary={
        <>
          <span className="promise-top"><VerdictTag v={p.verdict} />{p.actual && <span className="num actual">{p.actual}</span>}</span>
          <span className="promise-claim">{p.claim}</span>
          <span className="promise-meta">
            {p.scope === "segment" ? "Business-line target · " : ""}Said {p.call_label}
            {p.period ? ` · due ${p.period}` : ""}
            {p.deadline_moves ? ` · deadline moved ${p.deadline_moves.length - 1}×` : ""}
          </span>
        </>
      }
    >
      <Receipts p={p} />
      {p.verdict === "not_due" && (
        <button className="link pin" aria-pressed={pinned} onClick={toggle}>{pinned ? "Stop watching" : "Watch this promise"}</button>
      )}
    </Collapse>
  );
}

const RANGES: [string, string][] = [["1D", "1d"], ["1M", "1mo"], ["6M", "6mo"], ["1Y", "1y"], ["5Y", "5y"]];

function PriceBlock({ symbol }: { symbol: string }) {
  const [q, setQ] = useState<any>(null);
  const [range, setRange] = useState("1d");
  const [points, setPoints] = useState<{ d: string; c: number }[]>([]);

  useEffect(() => {
    let alive = true;
    const pull = () => api<any>(`/api/quote/${symbol}`).then((r) => alive && setQ(r)).catch(() => {});
    pull();
    // Quotes are cached for a minute on the server, and outside market hours nothing changes.
    const timer = marketMoment().label === "Market open" ? window.setInterval(pull, 60000) : undefined;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [symbol]);

  useEffect(() => {
    if (range === "1d") return;
    let alive = true;
    api<{ points: { d: string; c: number }[] }>(`/api/history/${symbol}?range=${range}`).then((r) => alive && setPoints(r.points)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [symbol, range]);

  if (!q) return <div className="priceblock skeleton" aria-hidden="true" />;
  const today = (q.today ?? []).map((c: number, i: number) => ({ d: q.today_times?.[i] ?? "Today", c }));
  return (
    <section className="priceblock" aria-label="Share price">
      <p className="price num">
        ₹{q.price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
        {q.change_pct != null && <span className={`price-change ${q.change_pct >= 0 ? "up" : "down"}`}>{signed(q.change_pct, 2)}</span>}
      </p>
      <p className="meta num">{q.as_of}{q.live ? " · Yahoo Finance, can be delayed" : " · last stored close"}</p>
      {q.low_52w != null && q.high_52w != null && <p className="meta num">52-week range ₹{q.low_52w.toLocaleString("en-IN")} to ₹{q.high_52w.toLocaleString("en-IN")}</p>}
      <PriceChart points={range === "1d" ? today : points} label={`${symbol} share price`} base={range === "1d" ? q.previous_close : undefined} />
      <div className="chips chips-tight" role="group" aria-label="Chart range">
        {RANGES.map(([label, id]) => <button key={id} className={`chip ${range === id ? "on" : ""}`} aria-pressed={range === id} onClick={() => setRange(id)}>{label}</button>)}
      </div>
    </section>
  );
}

function Kpis({ items }: { items: any[] }) {
  if (!items?.length) return null;
  return (
    <div id="kpis" className="sub">
      <h3 className="sub-h">What this company reports</h3>
      <p className="meta">The operating numbers management itself chose to give, in its own words.</p>
      <div className="promises">
        {items.map((k, i) => (
          <Collapse key={i} className="promise" summary={<span className="kpi"><span>{k.name}</span><b className="num">{k.value}</b></span>}>
            <figure className="quote">
              <blockquote>{k.quote}</blockquote>
              <figcaption>{k.speaker || "Management"}, {fromSource(k.call_label)} · <SourceLink url={k.url}>page {k.page}</SourceLink></figcaption>
            </figure>
          </Collapse>
        ))}
      </div>
    </div>
  );
}

function Peers({ p, open }: { p: any; open: (s: string) => void }) {
  if (!p?.rows?.length) return null;
  const pct = (v: number | null) => (v == null ? "–" : signed(v, 0));
  const n = (v: number | null, digits = 0) => (v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits }));
  // Enough to compare sizes, in a column a phone can fit.
  const cap = (v: number | null) => (v == null ? "–" : v >= 1000 ? `${n(v / 1000, 0)}k` : n(v));
  return (
    <section className="sec" id="peers">
      <h2>Against its peers</h2>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Peer comparison">
        <table className="results peers">
          <thead>
            <tr><th scope="col">Company</th><th scope="col">Cap ₹ cr</th><th scope="col">P/E</th><th scope="col">ROCE</th><th scope="col">Sales</th><th scope="col">Profit</th></tr>
          </thead>
          <tbody>
            {p.rows.map((r: any) => (
              <tr key={r.symbol ?? r.name} className={r.self ? "peer-self" : ""}>
                <th scope="row">{r.self || !r.symbol ? r.name : <button className="link" onClick={() => open(r.symbol)}>{r.name}</button>}</th>
                <td className="num">{cap(r.market_cap)}</td>
                <td className="num">{n(r.pe, 0)}</td>
                <td className="num">{r.roce == null ? "–" : `${n(r.roce, 0)}%`}</td>
                <td className={`num ${(r.sales_yoy ?? 0) > 0 ? "up" : (r.sales_yoy ?? 0) < 0 ? "down" : ""}`}>{pct(r.sales_yoy)}</td>
                <td className={`num ${(r.profit_yoy ?? 0) > 0 ? "up" : (r.profit_yoy ?? 0) < 0 ? "down" : ""}`}>{pct(r.profit_yoy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta">Sales and profit are the latest quarter, year on year. <SourceLink url={p.source}>Screener's peer table</SourceLink></p>
    </section>
  );
}

const EFFECTS: Record<string, string> = { headwind: "Headwind", tailwind: "Tailwind", neutral: "Noted" };

function Forces({ f }: { f: any }) {
  if (!f?.forces) return null;
  const group = (title: string, keys: string[]) => {
    const filled = keys.filter((k) => f.forces[k]?.length).length;
    return (
      <Collapse summary={<><b>{title}</b> <span className="count num">{filled} of {keys.length} discussed</span></>} className="tier">
        {keys.map((k) => (
          <div key={k} className="force">
            <h4 className="person-h">{f.labels[k]}</h4>
            {f.forces[k]?.length ? f.forces[k].map((x: any, i: number) => (
              <figure className="quote" key={i}>
                <blockquote>{x.quote}</blockquote>
                <figcaption>
                  <b className={x.effect === "headwind" ? "down" : x.effect === "tailwind" ? "up" : ""}>{EFFECTS[x.effect]}</b> · {x.speaker || "Management"}, {fromSource(x.call_label)} · <SourceLink url={x.url}>page {x.page}</SourceLink>
                </figcaption>
              </figure>
            )) : <p className="meta">Not discussed in what FINN read.</p>}
          </div>
        ))}
      </Collapse>
    );
  };
  return (
    <section className="sec" id="forces">
      <h2>Forces on the business</h2>
      <p className="meta">Porter's five forces and PESTLE, in management's own words. This shows what they talk about, and gaps may be what they avoid. It isn't an independent assessment of the sector, because that would need outside knowledge.</p>
      {group("Porter's five forces", f.porter)}
      {group("PESTLE", f.pestle)}
    </section>
  );
}

function TrackRecord({ d }: { d: Dossier }) {
  const pr = d.promises;
  if (!pr) {
    return (
      <section className="sec" id="record">
        <h2>Track record</h2>
        <p className="empty-note">No promise record for this company.</p>
      </section>
    );
  }
  const t = pr.tally;
  const kept = t.delivered, short = t.partly, missed = t.missed, tested = pr.testable;
  const ps: Promise_[] = pr.promises;
  const tested_list = ps.filter((p) => ["missed", "partly", "delivered"].includes(p.verdict));
  const open_list = ps.filter((p) => p.verdict === "not_due");
  const unverified = ps.filter((p) => p.verdict === "no_evidence");
  const vague = ps.filter((p) => p.verdict === "vague");
  return (
    <section className="sec" id="record">
      <h2>Track record</h2>
      {pr.source === "annual_reports" && <p className="meta">Built from annual reports, because this company has no readable earnings calls.</p>}
      {tested > 0 ? (
        <>
          <p className="score">
            Kept <b className="num">{kept}</b> of <b className="num">{tested}</b> promises we could check
          </p>
          <div className="segbar" role="img" aria-label={`${kept} delivered, ${short} fell short, ${missed} missed`}>
            {kept > 0 && <span style={{ flexGrow: kept, background: "var(--good)" }} />}
            {short > 0 && <span style={{ flexGrow: short, background: "var(--warn)" }} />}
            {missed > 0 && <span style={{ flexGrow: missed, background: "var(--bad)" }} />}
          </div>
          <ul className="legend">
            {(["delivered", "partly", "missed"] as Verdict[]).map((v) => (
              <li key={v} style={{ color: VERDICT[v].color }}>
                <span aria-hidden="true">{VERDICT[v].icon}</span> <span className="legend-text">{VERDICT[v].label} <b className="num">{t[v]}</b></span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="score">No promise here has reached its due date with evidence we could check.</p>
      )}
      {vague.length + unverified.length > tested && tested >= 0 && ps.length > 4 && (
        <p className="meta">{vague.length} of management's {ps.length} tracked statements gave no figure or date to hold them to.</p>
      )}

      {pr.moves?.length > 0 && (
        <div className="moves">
          <h3 className="eyebrow">Moved the goalposts</h3>
          {pr.moves.map((m: any, i: number) => (
            <p key={i} className="move">
              <b>{m.metric.replaceAll("_", " ")}, {m.period}:</b> <span className="num">{m.from}{m.unit === "%" ? "%" : ""} → {m.to}{m.unit === "%" ? "%" : ""}</span> between the {m.from_call} and {m.to_call} calls.{" "}
              <SourceLink url={m.to_url}>Source</SourceLink>
            </p>
          ))}
        </div>
      )}

      <div className="promises">{tested_list.map((p, i) => <PromiseRow key={i} p={p} symbol={d.symbol} />)}</div>

      {open_list.length > 0 && (
        <div id="guidance" className="sub">
          <h3 className="sub-h">Guidance still open <span className="count num">{open_list.length}</span></h3>
          <div className="promises">
            {[...open_list].sort((a, b) => (a.period ?? "z").localeCompare(b.period ?? "z")).map((p, i) => <PromiseRow key={i} p={p} symbol={d.symbol} />)}
          </div>
        </div>
      )}
      {unverified.length > 0 && (
        <Collapse summary={<>Couldn't verify <span className="count num">{unverified.length}</span></>} className="tier tier-quiet">
          <p className="meta">Promises past due, or undated, where later calls don't settle what happened. FINN says so instead of guessing.</p>
          <div className="promises">{unverified.map((p, i) => <PromiseRow key={i} p={p} symbol={d.symbol} />)}</div>
        </Collapse>
      )}
      {vague.length > 0 && (
        <Collapse summary={<>Too vague to test <span className="count num">{vague.length}</span></>} className="tier tier-quiet">
          <div className="promises">{vague.map((p, i) => <PromiseRow key={i} p={p} symbol={d.symbol} />)}</div>
        </Collapse>
      )}
    </section>
  );
}

function Arguments({ a }: { a: Dossier["arguments"] }) {
  if (!a) return null;
  const side = (title: string, items: any[], cls: string) => (
    <div className={`side ${cls}`}>
      <h3>{title}</h3>
      <ul>
        {items.map((x, i) => (
          <li key={i}>
            {x.point}
            <span className="cites">
              {x.sources
                .filter((s: any, j: number, all: any[]) => all.findIndex((o) => o.source === s.source) === j)
                .map((s: any) => <SourceLink key={s.source} url={s.url}>{s.source}</SourceLink>)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <section className="sec" id="case">
      <h2>Bull vs. bear</h2>
      {side("Bull", a.bull, "bull")}
      {side("Bear", a.bear, "bear")}
      {a.swing && <p className="swing"><span className="eyebrow">What decides it</span>{a.swing}</p>}
    </section>
  );
}

function Numbers({ d }: { d: Dossier }) {
  const q = d.snapshot?.quarter;
  const ratios = d.snapshot?.ratios ?? {};
  // ROCE isn't meaningful for a lender, since its raw material is borrowed money.
  const pick = ["Market Cap", "Stock P/E", ...(q?.metrics?.nii ? [] : ["ROCE"]), "ROE", "Dividend Yield", "Book Value"];
  const tr = d.trajectory;
  return (
    <section className="sec" id="numbers">
      <h2>The numbers</h2>
      {q && (
        <>
          <p className="meta">{q.label} · {q.basis} · filed XBRL</p>
          <div className="metrics">
            {Object.entries<Metric>(q.metrics).map(([k, m]) => (
              <div key={k} className="metric">
                <span className="metric-name">{METRIC_NAMES[k] ?? k}</span>
                <span className="metric-value num">{cr(m.value)}</span>
                <span className={`num metric-yoy ${(m.yoy ?? 0) > 0 ? "up" : (m.yoy ?? 0) < 0 ? "down" : ""}`}>{m.yoy === null ? "–" : `${signed(m.yoy)} YoY`}</span>
              </div>
            ))}
          </div>
          {q.sector_stats?.length > 0 && (
            <dl className="ratios">
              {q.sector_stats.map((s: any) => <div key={s.label}><dt>{s.label}</dt><dd className="num">{s.value.toLocaleString("en-IN")}{s.unit}</dd></div>)}
            </dl>
          )}
          {q.built && <p className="meta">{q.built}</p>}
        </>
      )}
      <dl className="ratios">
        {pick.filter((k) => ratios[k]).map((k) => (
          <div key={k}><dt>{k}</dt><dd className="num">{ratios[k].replace("Cr.", "cr")}</dd></div>
        ))}
      </dl>

      <Kpis items={d.kpis} />

      {tr && (
        <div id="trajectory" className="sub">
          <h3 className="sub-h">Trajectory</h3>
          <Bars title={tr.banking ? "Interest earned, ₹ crore" : "Revenue, ₹ crore"} labels={tr.points.years} values={tr.points.sales} format={cr} />
          {tr.points.opm.length > 0 && <Bars title="Operating margin" labels={tr.points.years} values={tr.points.opm} format={(v) => `${v}%`} tone="bad-negative" />}
          <Bars title="Net profit, ₹ crore" labels={tr.points.years} values={tr.points.profit} format={cr} tone="bad-negative" />
          <ul className="signals">
            {tr.signals.map((s: any) => (
              <li key={s.label}>
                <b>{s.label}</b> <span className="num">{s.c3 ?? "–"}% 3-yr, {s.c5 ?? "–"}% 5-yr CAGR</span>, {s.direction}
              </li>
            ))}
            {tr.margin_change_3y != null && <li><b>Margin</b> <span className="num">{tr.margin_now}%, {tr.margin_change_3y > 0 ? "+" : "−"}{Math.abs(tr.margin_change_3y)} pts</span> over 3 years</li>}
          </ul>
        </div>
      )}
    </section>
  );
}

function Balance({ b }: { b: Dossier["balance_sheet"] }) {
  if (!b) return null;
  return (
    <section className="sec" id="balance">
      <h2>Balance sheet</h2>
      {b.negative_net_worth && <p className="flag">Net worth is negative: liabilities exceed assets on the books.</p>}
      {b.banking ? (
        <>
          <Bars title="Deposits, ₹ crore" labels={b.columns} values={b.deposits} format={cr} />
          <Bars title="Borrowings, ₹ crore" labels={b.columns} values={b.debt} format={cr} />
        </>
      ) : (
        <>
          <Bars title="Debt, ₹ crore" labels={b.columns} values={b.debt} format={cr} />
          <Bars title="Debt-to-equity" labels={b.columns} values={b.de} format={(v) => `${v}×`} />
        </>
      )}
      {b.days && b.days.ccc?.some((x: any) => x !== null) && (
        <Bars title="Cash conversion cycle, days" labels={b.days.columns} values={b.days.ccc} format={(v) => `${v} days`} />
      )}
    </section>
  );
}

function Cash({ c }: { c: Dossier["cash"] }) {
  if (!c) return null;
  return (
    <section className="sec" id="cash">
      <h2>Cash quality</h2>
      {c.banking ? (
        <>
          <p className="meta">{c.note}</p>
          {c.npa?.gross?.some((x: any) => x !== null) && <Bars title="Gross NPA" labels={c.npa.columns} values={c.npa.gross} format={(v) => `${v}%`} />}
          {c.npa?.net?.some((x: any) => x !== null) && <Bars title="Net NPA" labels={c.npa.columns} values={c.npa.net} format={(v) => `${v}%`} />}
        </>
      ) : (
        <>
          {c.cfo_to_pat_5y !== null && (
            <p className="score">
              <b className="num">{c.cfo_to_pat_5y}×</b> operating cash to profit over 5 years. {c.verdict === "strong" ? "Profits are turning into cash." : c.verdict === "adequate" ? "Most profit turns into cash." : "Profit isn't turning into cash."}
            </p>
          )}
          <Bars title="Operating cash flow, ₹ crore" labels={c.columns} values={c.cfo} format={cr} tone="bad-negative" />
          <Bars title="Free cash flow, ₹ crore" labels={c.columns} values={c.fcf} format={cr} tone="bad-negative" />
        </>
      )}
    </section>
  );
}

function Business({ b }: { b: Dossier["business"] }) {
  if (!b) return null;
  return (
    <section className="sec" id="business">
      <h2>The business</h2>
      {b.description && (
        <p className="prose">
          {b.description} {b.source && <SourceLink url={b.source.url}>{b.source.label}{b.source.page ? `, p.${b.source.page}` : ""}</SourceLink>}
        </p>
      )}
      {b.segments?.length > 0 && (
        <ul className="mix">
          {b.segments.slice(0, 8).map((s: any) => (
            <li key={s.name}>
              <span className="mix-name">{s.name}</span>
              <span className="num mix-share">{s.share}%</span>
              <span className="mix-bar"><span style={{ width: `${s.share}%` }} /></span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Notes({ f }: { f: Dossier["ar_flags"] }) {
  if (!f?.reports?.length) return null;
  const r = f.reports[0];
  const lines: { text: string; page?: number }[] = [];
  if (r.auditor?.opinion && r.auditor.opinion !== "not_found") lines.push({ text: `Auditor's opinion: ${r.auditor.opinion}${r.auditor.emphasis ? `. Emphasised: ${r.auditor.emphasis}` : ""}.`, page: r.auditor.page });
  if (r.contingent_crore) lines.push({ text: `Contingent liabilities: ${cr(r.contingent_crore)}${f.contingent_change_pct != null ? `, ${signed(f.contingent_change_pct)} on the year` : ""}.`, page: r.contingent_page });
  if (r.rpt_note) lines.push({ text: `Related parties: ${r.rpt_note.replace(/\.+$/, "")}.`, page: r.rpt_page });
  if (!lines.length) return null;
  return (
    <section className="sec" id="notes">
      <h2>In the annual report's notes</h2>
      <ul className="notes">
        {lines.map((l, i) => (
          <li key={i}>{l.text} <SourceLink url={r.url}>AR {r.year}{l.page ? `, p.${l.page}` : ""}</SourceLink></li>
        ))}
      </ul>
    </section>
  );
}

function Ask({ symbol }: { symbol: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [a, setA] = useState<any>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    setA(null);
    try {
      setA(await api(`/api/research/${symbol}/ask?q=${encodeURIComponent(q.trim())}`));
    } catch {
      setA({ answer: null, error: "Couldn't answer that just now." });
    }
    setBusy(false);
  };
  return (
    <section className="sec" id="ask">
      <h2>Ask the transcripts</h2>
      <form className="ask" onSubmit={submit}>
        <label className="sr" htmlFor="askq">Question about what management said</label>
        <input id="askq" value={q} onChange={(e) => setQ(e.target.value)} placeholder="What did they say about margins?" />
        <button className="primary" disabled={busy}>{busy ? "Reading…" : "Ask"}</button>
      </form>
      {a && (
        <div className="answer">
          {a.error ? <p className="meta">{a.error}</p> : <p className="prose">{a.answer}</p>}
          {a.quotes?.map((x: any, i: number) => (
            <figure className="quote" key={i}>
              <blockquote>{x.text}</blockquote>
              <figcaption>{x.speaker || "Management"}, {x.call_label} call · <SourceLink url={x.url}>page {x.page}</SourceLink></figcaption>
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}

function DossierView({ d, onBack, open }: { d: Dossier; onBack: () => void; open: (s: string) => void }) {
  const candidates: [string, string, boolean][] = [
    ["record", "Track record", true], ["case", "Bull / bear", !!d.arguments], ["numbers", "Numbers", true], ["peers", "Peers", !!d.peers?.rows?.length], ["balance", "Balance sheet", !!d.balance_sheet],
    ["cash", "Cash", !!d.cash], ["forces", "Forces", !!d.forces?.forces], ["business", "Business", !!d.business], ["ask", "Ask", true],
  ];
  const sections = candidates.filter((c) => c[2]);
  return (
    <>
      <header className="dossier-head">
        <button className="link back" onClick={onBack}>← Research</button>
        <h1 className="dossier-name">{d.name}</h1>
        <p className="meta">{d.symbol}{d.business?.industry ? ` · ${d.business.industry}` : ""}{d.consolidated ? " · consolidated" : " · standalone"}</p>
      </header>
      <PriceBlock symbol={d.symbol} />
      <nav className="jump" aria-label="Sections">
        {sections.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
      </nav>
      {d.missing?.map((m: string) => <p key={m} className="flag">{m}</p>)}
      <TrackRecord d={d} />
      {d.dodged?.length > 0 && (
        <section className="sec" id="dodged">
          <h2>Questions they didn't answer</h2>
          {d.dodged.map((x: any, i: number) => (
            <div key={i} className="dodge">
              <p className="dodge-q">{x.asker}: “{x.question}”</p>
              <p className="dodge-a">{x.answerer}: “{x.answer}”</p>
              <p className="meta">{x.why} · <SourceLink url={x.url}>{x.call_label} call, p.{x.page}</SourceLink></p>
            </div>
          ))}
        </section>
      )}
      <Arguments a={d.arguments} />
      <Numbers d={d} />
      <Peers p={d.peers} open={open} />
      <Balance b={d.balance_sheet} />
      <Cash c={d.cash} />
      <Forces f={d.forces} />
      <Notes f={d.ar_flags} />
      <Business b={d.business} />
      <Ask symbol={d.symbol} />
      <p className="asof">
        Built from filed results, {d.promises?.source === "annual_reports" ? "annual reports" : `${d.promises?.calls?.length ?? 0} earnings-call transcripts and the annual report`}. Peers come from screener.in. No news, broker notes or outside knowledge.
      </p>
    </>
  );
}

export function ResearchScreen({ symbol, open }: { symbol: string | null; open: (s: string | null) => void }) {
  const [q, setQ] = useState("");
  const [state, setState] = useState<{ status: string; step?: string; error?: string; dossier?: Dossier } | null>(null);
  const [ready, setReady] = useState<string[]>([]);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    api<{ dossiers: string[] }>("/api/status").then((s) => setReady(s.dossiers)).catch(() => {});
  }, [symbol]);

  useEffect(() => {
    clearTimeout(timer.current);
    if (!symbol) {
      setState(null);
      return;
    }
    let alive = true;
    const poll = async () => {
      try {
        const s = await api<any>(`/api/research/${symbol}`);
        if (!alive) return;
        if (s.status === "none") {
          await api(`/api/research/${symbol}`, { method: "POST" });
          setState({ status: "running", step: "Starting" });
          timer.current = window.setTimeout(poll, 1500);
          return;
        }
        setState(s);
        if (s.status === "running") timer.current = window.setTimeout(poll, 1500);
      } catch {
        if (alive) setState({ status: "failed", error: "Couldn't reach FINN. Check your connection." });
      }
    };
    setState({ status: "loading" });
    poll();
    return () => {
      alive = false;
      clearTimeout(timer.current);
    };
  }, [symbol]);

  if (symbol && state?.status === "ready" && state.dossier) return <DossierView d={state.dossier} onBack={() => open(null)} open={(s) => open(s)} />;

  if (symbol && state?.status !== "failed") {
    return (
      <div className="working">
        <button className="link back" onClick={() => open(null)}>← Research</button>
        <h1 className="dossier-name">{symbol}</h1>
        <div className="pulse" aria-hidden="true" />
        <p aria-live="polite">{state?.step || "Opening"}…</p>
        <p className="meta">The first look at a company reads its last five earnings calls and annual report, so it takes a minute or two. After that it's instant.</p>
      </div>
    );
  }

  return (
    <>
      <header className="mast">
        <h1 className="lede">Did they do what they said?</h1>
        <p className="moment">Any NSE company, checked against its own filings, earnings calls and annual reports.</p>
      </header>
      {symbol && state?.status === "failed" && <p className="flag">{state.error}</p>}
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          const s = q.trim().toUpperCase();
          if (s) open(s);
        }}
      >
        <label className="sr" htmlFor="ticker">NSE ticker</label>
        <input id="ticker" value={q} autoCapitalize="characters" autoCorrect="off" spellCheck={false} onChange={(e) => setQ(e.target.value)} placeholder="NSE ticker, e.g. KAYNES" />
        <button className="primary">Research</button>
      </form>
      {ready.length > 0 && (
        <>
          <h2 className="eyebrow">Ready now</h2>
          <div className="chips chips-wrap">
            {ready.map((s) => <button key={s} className="chip" onClick={() => open(s)}>{s}</button>)}
          </div>
          <CompareSection ready={ready} open={(s) => open(s)} />
        </>
      )}
    </>
  );
}
