import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DEFAULT_SETTINGS, METRIC_NAMES, ago, api, cr, day, load, marketMoment, mine, save, signed, type Brief, type Event, type Holding, type Person, type Pin, type ResultsCard, type Settings } from "./lib";
import { MovingNow, alertsPath, type Alert } from "./Chat";
import { Collapse, SourceLink, VerdictTag } from "./ui";

const WINDOWS: [string, number][] = [["24 hours", 24], ["3 days", 72], ["7 days", 168], ["30 days", 720]];
interface Span { from: string; to: string }
const isoDay = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

interface Mover { symbol: string; date: string; move: number; volX: number | null; because: string[]; related?: string }

function movers(holdings: Holding[], settings: Settings, hours: number, span: Span | null): Mover[] {
  const out: Mover[] = [];
  for (const h of holdings) {
    // Prices are end-of-day, so a 24-hour window still means the latest close.
    const sessions = span
      ? h.series.filter((s) => s.d >= span.from && s.d <= span.to)
      : hours <= 24 ? h.series.slice(-1) : h.series.filter((s) => (Date.now() - new Date(s.d).getTime()) / 3.6e6 <= hours + 24);
    for (const s of sessions) {
      const because: string[] = [];
      if (Math.abs(s.m) >= settings.movePct) because.push(`moved ${signed(s.m)}`);
      if (s.ok && s.x !== null && s.x >= settings.volX) because.push(`volume ${s.x.toFixed(1)}×`);
      if (!because.length) continue;
      const near = h.filings.find((f) => Math.abs(new Date(f.d).getTime() - new Date(s.d).getTime()) <= 864e5);
      out.push({ symbol: h.symbol, date: s.d, move: s.m, volX: s.x, because, related: near?.title });
    }
  }
  return out.sort((a, b) => Math.abs(b.move) - Math.abs(a.move));
}

function ResultsTable({ r }: { r: ResultsCard }) {
  return (
    <>
      <table className="results">
        <caption>
          {r.label} · {r.basis} · {r.audited ? "audited" : "unaudited"}
        </caption>
        <thead>
          <tr><th scope="col">₹ crore</th><th scope="col">{r.label}</th><th scope="col">QoQ</th><th scope="col">YoY</th></tr>
        </thead>
        <tbody>
          {Object.entries(r.metrics).map(([k, m]) => (
            <tr key={k}>
              <th scope="row">{METRIC_NAMES[k] ?? k}</th>
              <td className="num">{m.value === null ? "–" : `${m.value < 0 ? "−" : ""}${Math.abs(m.value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`}</td>
              <td className={`num ${tone(m.qoq)}`}>{m.qoq === null ? changeWord(m.value, m.prev) : signed(m.qoq)}</td>
              <td className={`num ${tone(m.yoy)}`}>{m.yoy === null ? changeWord(m.value, m.year_ago) : signed(m.yoy)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.margin != null && <p className="meta num">EBITDA margin {r.margin}%</p>}
      {r.sector_stats && r.sector_stats.length > 0 && <p className="meta num">{r.sector_stats.map((s) => `${s.label} ${s.value.toLocaleString("en-IN")}${s.unit}`).join(" · ")}</p>}
      {r.built && <p className="meta">{r.built}</p>}
      {r.flags.map((f) => <p key={f.kind} className="flag">{f.text}</p>)}
    </>
  );
}

const tone = (v: number | null) => (v === null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

/** Percent change from or to a loss is meaningless, so say what happened instead. */
function changeWord(now: number | null, then: number | null) {
  if (now === null || then === null) return "–";
  if (now < 0 && then < 0) return "loss";
  if (now < 0) return "to loss";
  if (then < 0) return "to profit";
  return "–";
}

function PersonBrief({ p, company }: { p: Person; company: string }) {
  const c = p.context;
  const facts = [p.age ? `Age ${p.age}` : null, ...(p.qualifications ?? [])].filter(Boolean).join(" · ");
  const records: string[] = [];
  if (c?.top_changes_12m) records.push(`${c.top_changes_12m} changes in the top team here in 12 months`);
  if (c?.on_calls && c.on_calls.of > 0) records.push(c.on_calls.spoke ? `Spoke on ${c.on_calls.spoke} of the last ${c.on_calls.of} earnings calls` : `Not heard on the last ${c.on_calls.of} earnings calls`);
  return (
    <div className="person">
      <p className="person-name">{p.name}</p>
      <p className="person-role">
        {p.role}
        {p.effective ? ` · ${p.action === "leaving" ? "leaves" : "from"} ${/^\d{4}-\d{2}-\d{2}/.test(p.effective) ? day(p.effective) : p.effective}` : ""}
      </p>
      {facts && <p className="person-facts">{facts}</p>}
      {p.replaces && <p className="meta">Succeeds {p.replaces}.</p>}
      {p.career && p.career.length > 0 && (
        <>
          <h4 className="person-h">Career</h4>
          <ul>{p.career.map((x) => <li key={x}>{x}</li>)}</ul>
        </>
      )}
      {!p.career?.length && p.profile && p.profile.length > 0 && <ul>{p.profile.map((x) => <li key={x}>{x}</li>)}</ul>}
      {p.other_boards && p.other_boards.length > 0 && <p className="meta"><b>Also on the boards of</b> {p.other_boards.join(", ")}.</p>}
      {p.time_at_company && <p className="meta">{p.time_at_company}.</p>}
      {p.term && <p className="meta">Term: {p.term}.</p>}
      {p.reason && <p className="meta">Stated reason: {p.reason}</p>}
      {records.map((x) => <p key={x} className="meta">{x}.</p>)}
      {c?.reaction && (
        <p className="meta num">
          Stock on {day(c.reaction.date)}: <span className={c.reaction.move_pct >= 0 ? "up" : "down"}>{signed(c.reaction.move_pct)}</span>{c.reaction.vol_x != null ? ` · ${c.reaction.vol_x.toFixed(1)}× volume` : ""}
        </p>
      )}
      <p className="person-links">
        {p.source && <SourceLink url={p.source.url}>{p.source.label}, p.{p.source.page}</SourceLink>}
        {p.action !== "leaving" && <SourceLink url={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${p.name} ${company}`)}`}>Find on LinkedIn</SourceLink>}
      </p>
    </div>
  );
}

function EventBody({ e, open, action }: { e: Event; open: (s: string) => void; action?: ReactNode }) {
  const d = e.detail;
  return (
    <>
      {e.evidence && e.evidence.lift >= 1.2 && (
        <p className="evidence">
          Filings like this moved the stock sharply <b className="num">{Math.round(e.evidence.big_move_pct)}%</b> of the time, against <b className="num">{Math.round(e.evidence.quiet_pct)}%</b> on a quiet day. <span className="num">{e.evidence.n.toLocaleString("en-IN")}</span> filings studied.
        </p>
      )}
      {e.results && <ResultsTable r={e.results} />}
      {e.promise_check && e.promise_check.checked.length > 0 && (
        <div className="check">
          <h4>Against what they promised</h4>
          {e.promise_check.checked.map((p, i) => (
            <div key={i} className="check-row">
              <VerdictTag v={p.verdict} />
              <p>{p.claim}{p.actual ? <b className="num"> Actual {p.actual}.</b> : null}</p>
            </div>
          ))}
        </div>
      )}
      {e.promise_check && e.promise_check.upcoming.length > 0 && (
        <p className="meta">Next due: {e.promise_check.upcoming.map((p) => `${p.claim} (${p.period})`).join("; ")}</p>
      )}
      {d.person && <PersonBrief p={d.person} company={e.name} />}
      {d.rating && (
        <p className="meta">
          {d.rating.agency}: {d.rating.rating}
          {d.rating.outlook ? ` · ${d.rating.outlook} outlook` : ""}
        </p>
      )}
      {d.legal && <p className="meta">{d.legal.authority}. {d.legal.company_view ? `Company says: ${d.legal.company_view}` : ""}</p>}
      {d.response && <p className="meta">Company's reply: {d.response}</p>}
      {d.amount_crore != null && (
        <p className="meta num">
          {cr(d.amount_crore)}{d.amount_note ? ` · ${d.amount_note}` : ""}
        </p>
      )}
      {e.filings.length > 1 && (
        <Collapse summary={`${e.filings.length} filings in this story`} className="thread">
          <ul className="thread-list">
            {e.filings.map((f) => (
              <li key={f.id}>
                <span className="num when">{ago(f.ts)}</span>
                {f.pdf_url ? <a href={f.pdf_url} target="_blank" rel="noopener noreferrer">{f.title}</a> : <span>{f.title}</span>}
              </li>
            ))}
          </ul>
        </Collapse>
      )}
      <div className="card-foot">
        <SourceLink url={e.pdf_url}>Filing</SourceLink>
        <button className="link" onClick={() => open(e.symbol)}>Track record</button>
        {action}
      </div>
    </>
  );
}

function TopCard({ e, done, onDone, open, pinned }: { e: Event; done: boolean; onDone: () => void; open: (s: string) => void; pinned?: Pin }) {
  return (
    <article className={`card ${done ? "card-done" : ""}`}>
      <header className="card-head">
        <button className="ticker" onClick={() => open(e.symbol)}>{e.symbol}</button>
        <span className="kind">{e.category_label}</span>
        {e.detail.tone === "negative" && <span className="tone-neg">Negative</span>}
        {e.detail.tone === "positive" && <span className="tone-pos">Positive</span>}
        <span className="when num">{ago(e.ts)}</span>
      </header>
      <h3>{e.title}</h3>
      {!done && (
        <>
          <p className="why">{e.reason}</p>
          {pinned && <p className="pinned">You're watching: {pinned.claim}</p>}
          <EventBody e={e} open={open} action={<button className="done" aria-pressed={false} onClick={onDone}>Done</button>} />
        </>
      )}
      {done && <button className="done done-undo" aria-pressed={true} onClick={onDone}>Undo</button>}
    </article>
  );
}

function listen(top: Event[]) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const lines = top.slice(0, 8).map((e) => `${e.name.replace(/\b(Ltd|Limited)\b\.?/i, "")}. ${e.title}.`);
  const u = new SpeechSynthesisUtterance(`${top.length} things matter. ${lines.join(" ")} That's everything.`);
  u.lang = "en-IN";
  u.rate = 1.05;
  speechSynthesis.speak(u);
}

/** The nearest thing to a push while the app is closed: the first thing you see on coming back. */
function SinceLastVisit({ settings, open, goChat }: { settings: Settings; open: (s: string) => void; goChat: () => void }) {
  const [fresh, setFresh] = useState<Alert[]>([]);
  useEffect(() => {
    const seen = load<string>("finn.brief.seen", "");
    api<{ messages: Alert[] }>(alertsPath(settings)).then((r) => {
      if (seen) setFresh(r.messages.filter((m) => m.ts > seen));
      if (r.messages[0]) save("finn.brief.seen", r.messages[0].ts);
    }).catch(() => {});
  }, []);
  if (!fresh.length) return null;
  return (
    <section className="since" aria-label="New since your last visit">
      <h2 className="eyebrow">New since you last looked · {fresh.length}</h2>
      <ul>
        {fresh.slice(0, 4).map((a) => (
          <li key={`${a.symbol}${a.ts}${a.title}`}>
            <button className="ticker" onClick={() => open(a.symbol)}>{a.symbol}</button>
            <span className={a.kind === "move" ? `num ${(a.move ?? 0) >= 0 ? "up" : "down"}` : ""}>{a.title}</span>
          </li>
        ))}
      </ul>
      {fresh.length > 4 && <button className="link" onClick={goChat}>See all {fresh.length} in Chat</button>}
    </section>
  );
}

interface Upcoming { symbol: string; date: string; kind: string; title: string; due?: string[] }

/** Only dates a company has filed with the exchange. FINN doesn't estimate when results will come. */
function ComingUp({ open }: { open: (s: string) => void }) {
  const [events, setEvents] = useState<Upcoming[]>([]);
  const [all, setAll] = useState(false);
  useEffect(() => {
    api<{ events: Upcoming[] }>(mine("/api/calendar?days=14")).then((r) => setEvents(r.events)).catch(() => {});
  }, []);
  if (!events.length) return null;
  const shown = all ? events : events.slice(0, 4);
  return (
    <section className="coming" aria-label="Coming up">
      <h2 className="eyebrow">Coming up · next 14 days</h2>
      <ul>
        {shown.map((e) => (
          <li key={e.symbol + e.date + e.kind}>
            <span className="when num">{day(e.date)}</span>
            <button className="ticker" onClick={() => open(e.symbol)}>{e.symbol}</button>
            <span>{e.title}</span>
            {e.due && e.due.length > 0 && <p className="meta coming-due">They promised for this quarter: {e.due.join("; ")}</p>}
          </li>
        ))}
      </ul>
      {events.length > shown.length && <button className="link" onClick={() => setAll(true)}>Show {events.length - shown.length} more</button>}
    </section>
  );
}

function NotableRow({ e, open }: { e: Event; open: (s: string) => void }) {
  return (
    <Collapse
      className="row"
      summary={<><span className="ticker-plain">{e.symbol}</span> {e.title} <span className="when num">{ago(e.ts)}</span></>}
    >
      <p className="why">{e.reason}</p>
      <EventBody e={e} open={open} />
    </Collapse>
  );
}

export function BriefScreen({ open, goChat }: { open: (s: string) => void; goChat: () => void }) {
  const [hours, setHours] = useState(() => load<number>("finn.hours", 24));
  const [span, setSpan] = useState<Span | null>(() => load<Span | null>("finn.span", null));
  const [custom, setCustom] = useState(() => load<Span | null>("finn.span", null) !== null);
  // Most days nothing in a portfolio ranks top, and that's a poor first impression of a brief. So only on someone's
  // very first look, an empty 24 hours opens on the last 7 days and says so. After that, an empty day stays honest.
  const firstLook = useRef(load<number | null>("finn.hours", null) === null && load<Span | null>("finn.span", null) === null);
  const [widened, setWidened] = useState(false);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [error, setError] = useState(false);
  const [done, setDone] = useState<string[]>(() => load("finn.done", []));
  const [speaking, setSpeaking] = useState(false);
  const settings = load<Settings>("finn.settings", DEFAULT_SETTINGS);
  const pins = load<Pin[]>("finn.pins", []);
  const moment = marketMoment();

  useEffect(() => {
    setBrief(null);
    setError(false);
    api<Brief>(mine(span ? `/api/brief?since=${span.from}&until=${span.to}` : `/api/brief?hours=${hours}`)).then((b) => {
      if (firstLook.current && !b.top.length) {
        firstLook.current = false;
        setWidened(true);
        setHours(168);
        return;
      }
      firstLook.current = false;
      setBrief(b);
    }).catch(() => setError(true));
    api<{ companies: Holding[] }>(mine("/api/portfolio")).then((p) => setHoldings(p.companies)).catch(() => {});
    // The wider view is for this visit only, so tomorrow's brief still opens on 24 hours.
    if (!widened) save("finn.hours", hours);
    save("finn.span", span);
  }, [hours, span?.from, span?.to]);

  const today = isoDay(new Date());
  const pickWindow = (h: number) => {
    firstLook.current = false;
    setWidened(false);
    save("finn.hours", h);
    setCustom(false);
    setSpan(null);
    setHours(h);
  };
  const setDate = (patch: Partial<Span>) => {
    firstLook.current = false;
    setWidened(false);
    const next = { from: span?.from ?? isoDay(new Date(Date.now() - 7 * 864e5)), to: span?.to ?? today, ...patch };
    // Picking a start after the end moves the end along, so the range is never backwards.
    if (next.from > next.to) next.to = next.from;
    setSpan(next);
  };

  // Muted categories drop out of the top list rather than disappearing: the investor chose that, not FINN.
  const { top, notable } = useMemo(() => {
    if (!brief) return { top: [] as Event[], notable: [] as Event[] };
    const muted = new Set(settings.muted);
    const pinnedSymbols = new Set(pins.map((p) => p.symbol));
    const rank = (a: Event, b: Event) => Number(pinnedSymbols.has(b.symbol)) - Number(pinnedSymbols.has(a.symbol)) || b.score - a.score || b.ts.localeCompare(a.ts);
    return {
      top: brief.top.filter((e) => !muted.has(e.category)).sort(rank),
      notable: [...brief.notable, ...brief.top.filter((e) => muted.has(e.category))].sort(rank),
    };
  }, [brief]);

  const moved = useMemo(() => movers(holdings, settings, hours, span), [holdings, hours, span?.from, span?.to]);
  const toggle = (id: string) => {
    const next = done.includes(id) ? done.filter((x) => x !== id) : [...done, id];
    setDone(next);
    save("finn.done", next.slice(-400));
  };
  const finished = top.filter((e) => done.includes(e.id)).length;
  const allDone = top.length > 0 && finished === top.length;
  // Press releases are the most common kind of filing here, so they get a shelf of their own.
  const news = notable.filter((e) => e.category === "press_release");
  const grouped = news.length >= 3;
  const hidden = brief ? brief.routine.reduce((n, e) => n + e.filings.length, 0) : 0;
  // One close is a glance. Over a longer window the list runs long, and the filings it promises shouldn't sit below it.
  const latestClose = hours <= 24 && !span;
  const moverList = brief && moved.length > 0 && (
    <section aria-label="Price and volume alerts" className="movers">
      <h2 className="eyebrow">Crossed your limits{latestClose ? ` · close of ${day(moved[0].date)}` : ""}</h2>
      {moved.slice(0, 6).map((m) => (
        <div key={`${m.symbol}${m.date}`} className="mover">
          <button className="ticker" onClick={() => open(m.symbol)}>{m.symbol}</button>
          <span className="num mover-figs">
            <span className={m.move >= 0 ? "up" : "down"}>{signed(m.move)}</span>
            {m.volX !== null && <span> · {m.volX.toFixed(1)}× vol</span>}
          </span>
          {!latestClose && <span className="when mover-date">{day(m.date)}</span>}
          {m.related && <p className="mover-note">Filed around then: {m.related}</p>}
        </div>
      ))}
    </section>
  );

  return (
    <>
      <header className="mast">
        <div className="mast-row">
          <span className="wordmark">FINN</span>
          <span className="mast-date">{new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}</span>
        </div>
        <p className="moment">{moment.label} · {moment.note}</p>
        {brief ? (
          <h1 className="lede">
            {top.length === 0 ? "Nothing needs you." : top.length === 1 ? "One thing matters." : `${top.length} things matter.`}{" "}
            <span className="lede-quiet">{brief.total_filings} {brief.total_filings === 1 ? "filing" : "filings"} read for you.</span>
          </h1>
        ) : !error ? <h1 className="lede lede-quiet">Reading your filings…</h1> : null}
        {span && <p className="moment num">{day(span.from)} to {day(span.to)}</p>}
        {widened && brief && <p className="moment">A quiet 24 hours, so here are the last 7 days.</p>}
        {top.length > 0 && (
          <div className="progress" role="progressbar" aria-valuenow={finished} aria-valuemin={0} aria-valuemax={top.length} aria-label="Items read">
            <div style={{ width: `${(finished / top.length) * 100}%` }} />
          </div>
        )}
        <div className="chips" role="group" aria-label="Time window">
          {WINDOWS.map(([label, h]) => (
            <button key={h} className={`chip ${h === hours && !custom ? "on" : ""}`} aria-pressed={h === hours && !custom} onClick={() => pickWindow(h)}>{label}</button>
          ))}
          <button className={`chip ${custom ? "on" : ""}`} aria-pressed={custom} onClick={() => { setCustom(true); setDate({}); }}>Custom</button>
          {top.length > 0 && "speechSynthesis" in window && (
            <button
              className="chip chip-listen"
              onClick={() => {
                if (speaking) speechSynthesis.cancel();
                else listen(top);
                setSpeaking(!speaking);
              }}
            >
              {speaking ? "Stop" : "Listen"}
            </button>
          )}
        </div>
        {custom && (
          <div className="daterange">
            <label>From <input type="date" id="date-from" value={span?.from ?? ""} min={brief?.meta.oldest_filing ?? undefined} max={today} onChange={(e) => e.target.value && setDate({ from: e.target.value })} /></label>
            <label>To <input type="date" id="date-to" value={span?.to ?? ""} min={span?.from} max={today} onChange={(e) => e.target.value && setDate({ to: e.target.value })} /></label>
            {brief?.meta.oldest_filing && <p className="meta">FINN holds filings from {new Date(brief.meta.oldest_filing).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}.</p>}
          </div>
        )}
      </header>

      <SinceLastVisit settings={settings} open={open} goChat={goChat} />

      <MovingNow settings={settings} open={open} />

      {error && (
        <div className="empty">
          <h2>Couldn't reach FINN</h2>
          <p>The last brief will appear here once you're back online.</p>
          <button className="primary" onClick={() => location.reload()}>Try again</button>
        </div>
      )}

      {latestClose && moverList}

      {!brief && !error && <div aria-hidden="true">{[0, 1, 2].map((i) => <div key={i} className="skeleton skeleton-card" />)}</div>}

      {brief && (
        <section aria-label="What matters">
          {top.map((e) => (
            <TopCard key={e.id} e={e} done={done.includes(e.id)} onDone={() => toggle(e.id)} open={open} pinned={pins.find((p) => p.symbol === e.symbol)} />
          ))}
        </section>
      )}

      {brief && top.length === 0 && (
        <div className="empty">
          <p>Nothing across your holdings rose to the top in this window.</p>
          {brief.latest_notable && (
            <div className="lastmatter">
              <p className="eyebrow">The last thing that mattered · {ago(brief.latest_notable.ts)}</p>
              <p><button className="ticker" onClick={() => open(brief.latest_notable!.symbol)}>{brief.latest_notable.symbol}</button> {brief.latest_notable.title}</p>
              <button className="link widen" onClick={() => pickWindow(168)}>See the last 7 days</button>
            </div>
          )}
        </div>
      )}

      {!latestClose && moverList}

      {allDone && (
        <div className="endnote">
          <p className="endnote-big">That's everything.</p>
          <p>You can put this down.</p>
        </div>
      )}

      {brief && <ComingUp open={open} />}

      {brief && notable.length > 0 && (
        <Collapse summary={<><b>Worth knowing</b> <span className="count num">{notable.length}</span></>} defaultOpen={top.length === 0} className="tier">
          {notable.filter((e) => !grouped || e.category !== "press_release").map((e) => <NotableRow key={e.id} e={e} open={open} />)}
          {grouped && (
            <Collapse className="row" summary={<><b>Company news</b> <span className="count num">{news.length}</span></>}>
              <p className="meta">The company's own press releases, in order of how often that kind has moved a stock.</p>
              {news.map((e) => <NotableRow key={e.id} e={e} open={open} />)}
            </Collapse>
          )}
        </Collapse>
      )}

      {brief && brief.routine.length > 0 && (
        <Collapse summary={<><b>Buried for you</b> <span className="count num">{hidden} filings</span></>} className="tier tier-quiet">
          <p className="meta">Newspaper ads, trading-window notices, compliance certificates and similar. FINN read them so you don't have to.</p>
          <ul className="thread-list">
            {brief.routine.flatMap((e) => e.filings.map((f) => (
              <li key={f.id}>
                <span className="ticker-plain">{e.symbol}</span>
                {f.pdf_url ? <a href={f.pdf_url} target="_blank" rel="noopener noreferrer">{f.title}</a> : <span>{f.title}</span>}
              </li>
            )))}
          </ul>
        </Collapse>
      )}

      {brief?.meta.last_refresh && (
        <p className="asof">
          Filings checked {ago(brief.meta.last_refresh)}
          {brief.meta.unreached?.length ? `. Couldn't reach ${brief.meta.unreached.join(", ")} on the last try.` : ""}
        </p>
      )}
    </>
  );
}
