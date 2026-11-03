import { useEffect, useMemo, useState, type FormEvent } from "react";
import { DEFAULT_SETTINGS, MAX_HOLDINGS, api, day, fromSource, load, marketMoment, mine, myHoldings, save, signed, type Holding, type Pin, type Settings } from "./lib";
import { SourceLink, Spark } from "./ui";

const MUTABLE: [string, string][] = [
  ["results", "Results"], ["management_change", "Management"], ["M&A", "M&A"], ["fund_raise", "Fund raises"], ["litigation", "Legal & regulatory"],
  ["credit_rating", "Credit ratings"], ["orders", "Order wins"], ["capex", "Capex"], ["rumour", "Rumour checks"], ["pledging", "Pledging"],
];
const LOOKBACK = 30; // sessions
const study = { filings: "2.8 lakh", companies: "2,284" };

const toKey = (b64: string) => Uint8Array.from(atob(b64.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

/** Shown only once the server has notification keys and the browser can receive them. */
function PhoneAlerts({ settings }: { settings: Settings }) {
  const [key, setKey] = useState<string | null>(null);
  const [on, setOn] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    api<{ enabled: boolean; public_key: string | null }>("/api/push").then((r) => r.enabled && setKey(r.public_key)).catch(() => {});
    navigator.serviceWorker.ready.then((w) => w.pushManager.getSubscription()).then((s) => setOn(!!s)).catch(() => {});
  }, []);

  if (!key) return null;
  const send = (subscription: PushSubscription) => api("/api/push/subscribe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subscription: subscription.toJSON(), move: settings.movePct, vol: settings.volX, muted: settings.muted }),
  });
  const toggle = async () => {
    setNote("");
    try {
      const worker = await navigator.serviceWorker.ready;
      const current = await worker.pushManager.getSubscription();
      if (current) {
        await api("/api/push/unsubscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: current.endpoint }) });
        await current.unsubscribe();
        setOn(false);
        return;
      }
      if ((await Notification.requestPermission()) !== "granted") {
        setNote("Notifications are blocked for this site in your browser settings.");
        return;
      }
      await send(await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: toKey(key) }));
      setOn(true);
    } catch {
      setNote("That didn't work on this browser. On an iPhone, add FINN to your home screen first.");
    }
  };
  return (
    <section className="sec">
      <h2>Phone notifications</h2>
      <p className="meta">Top filings and anything crossing your limits, even with the app closed. They use the limits above as they stood when you turned this on.</p>
      <button className={`chip ${on ? "on" : ""}`} aria-pressed={on} onClick={toggle}>{on ? "On" : "Turn on"}</button>
      {note && <p className="flag">{note}</p>}
    </section>
  );
}

interface Quote { symbol: string; name: string; price: number | null; change_pct: number | null; as_of: string | null; today: number[] }

function LivePrices({ open, onChange, version }: { open: (s: string) => void; onChange: () => void; version: number }) {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [editing, setEditing] = useState(false);
  const [ticker, setTicker] = useState("");
  const [note, setNote] = useState("");
  const [adding, setAdding] = useState(false);
  const store = (next: string[] | null) => {
    save("finn.holdings", next);
    onChange();
  };
  const remove = (symbol: string) => {
    setNote("");
    store(quotes.map((q) => q.symbol).filter((s) => s !== symbol));
  };
  const add = async (e: FormEvent) => {
    e.preventDefault();
    const symbol = ticker.trim().toUpperCase();
    if (!symbol || adding) return;
    if (quotes.some((q) => q.symbol === symbol)) {
      setNote(`${symbol} is already on your list.`);
      return;
    }
    setAdding(true);
    setNote("");
    try {
      await api(`/api/track/${encodeURIComponent(symbol)}`, { method: "POST" });
      store([...quotes.map((q) => q.symbol), symbol]);
      setTicker("");
      setNote(`Added ${symbol}. Its filings and price history arrive in a minute or two.`);
    } catch (err) {
      setNote(String(err).includes("404") ? `Couldn't find ${symbol} on NSE. Check the ticker.` : "Couldn't add that just now. Try again in a minute.");
    }
    setAdding(false);
  };
  useEffect(() => {
    let alive = true;
    const pull = () => api<{ quotes: Quote[] }>(mine("/api/quotes")).then((r) => alive && setQuotes(r.quotes)).catch(() => {});
    pull();
    const timer = marketMoment().label === "Market open" ? window.setInterval(pull, 60000) : undefined;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [version]);
  const asOf = quotes.find((q) => q.as_of)?.as_of;
  return (
    <section className="sec sec-first" aria-label="Your holdings">
      <div className="sec-head">
        <h2>Your holdings</h2>
        <button className="link" aria-pressed={editing} onClick={() => { setEditing(!editing); setNote(""); }}>{editing ? "Done" : "Edit"}</button>
      </div>
      {editing && (
        <form className="ask add-holding" onSubmit={add}>
          <label className="sr" htmlFor="holding">NSE ticker to add</label>
          <input id="holding" value={ticker} autoCapitalize="characters" autoCorrect="off" spellCheck={false} onChange={(e) => setTicker(e.target.value)} placeholder="Add an NSE ticker" />
          <button className="primary" disabled={adding || !ticker.trim() || quotes.length >= MAX_HOLDINGS}>{adding ? "Checking" : "Add"}</button>
        </form>
      )}
      {note && <p className="meta" role="status">{note}</p>}
      {quotes.length === 0 ? <div className="skeleton skeleton-card" aria-hidden="true" /> : (
        <ul className="quotes">
          {quotes.map((q) => (
            <li key={q.symbol} className={editing ? "quote-editing" : ""}>
              <button className="quote-row" onClick={() => open(q.symbol)}>
                <span className="ticker-plain">{q.symbol}</span>
                <Spark values={q.today} up={(q.change_pct ?? 0) >= 0} />
                <span className="num quote-price">{q.price === null ? "–" : `₹${q.price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}</span>
                <span className={`num quote-change ${(q.change_pct ?? 0) >= 0 ? "up" : "down"}`}>{q.change_pct === null ? "–" : signed(q.change_pct, 2)}</span>
              </button>
              {editing && quotes.length > 1 && <button className="link" aria-label={`Remove ${q.symbol}`} onClick={() => remove(q.symbol)}>Remove</button>}
            </li>
          ))}
        </ul>
      )}
      {editing && myHoldings() && <button className="link widen" onClick={() => { store(null); setNote(""); }}>Back to the default list</button>}
      {editing && <p className="meta">Up to {MAX_HOLDINGS} companies. Your list stays on this device.</p>}
      {asOf && <p className="meta num">As of {asOf}. Yahoo Finance prices can be delayed, so check your broker before you trade.</p>}
    </section>
  );
}

interface Hit { symbol: string; d: string; m: number; x: number | null; note?: string }

function hits(holdings: Holding[], volX: number, movePct: number): Hit[] {
  const out: Hit[] = [];
  for (const h of holdings) {
    for (const s of h.series.slice(-LOOKBACK)) {
      if (Math.abs(s.m) >= movePct || (s.ok && s.x !== null && s.x >= volX)) out.push({ symbol: h.symbol, d: s.d, m: s.m, x: s.x, note: h.filings.find((f) => Math.abs(new Date(f.d).getTime() - new Date(s.d).getTime()) <= 864e5)?.title });
    }
  }
  return out.sort((a, b) => b.d.localeCompare(a.d));
}

export function WatchScreen({ open }: { open: (s: string) => void }) {
  const [settings, setSettings] = useState<Settings>(() => ({ ...DEFAULT_SETTINGS, ...load<Partial<Settings>>("finn.settings", {}) }));
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [version, setVersion] = useState(0);
  const [pins, setPins] = useState<Pin[]>(() => load("finn.pins", []));

  const [status, setStatus] = useState<{ filings: number; llm_calls: number; llm_usd: number; model: string | null } | null>(null);

  useEffect(() => {
    api<{ companies: Holding[] }>(mine("/api/portfolio")).then((p) => setHoldings(p.companies)).catch(() => {});
    api<any>("/api/status").then(setStatus).catch(() => {});
  }, [version]);

  const update = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    save("finn.settings", next);
  };

  const list = useMemo(() => hits(holdings, settings.volX, settings.movePct), [holdings, settings.volX, settings.movePct]);
  const perWeek = list.length / (LOOKBACK / 5);

  return (
    <>
      <header className="mast">
        <h1 className="lede">You decide what's unusual.</h1>
        <p className="moment">Live prices for your holdings, and the limits that decide what interrupts you.</p>
      </header>

      <LivePrices open={open} version={version} onChange={() => setVersion((v) => v + 1)} />

      <section className="sec" aria-label="Alert limits">
        <h2>Your limits</h2>
        <p className="meta">FINN shows what each setting would have cost you in interruptions.</p>
        <div className="slider">
          <label htmlFor="vol">Volume above <b className="num">{settings.volX.toFixed(1)}×</b> its 20-day average</label>
          <input id="vol" type="range" min={1.2} max={6} step={0.1} value={settings.volX} onChange={(e) => update({ volX: Number(e.target.value) })} />
        </div>
        <div className="slider">
          <label htmlFor="move">Price move beyond <b className="num">±{settings.movePct.toFixed(1)}%</b> in a day</label>
          <input id="move" type="range" min={1} max={15} step={0.5} value={settings.movePct} onChange={(e) => update({ movePct: Number(e.target.value) })} />
        </div>
        <div className="budget" aria-live="polite">
          <p className="budget-num num">{list.length}</p>
          <p>
            alerts across your portfolio in the last {LOOKBACK} sessions, about <b className="num">{perWeek.toFixed(perWeek < 10 ? 1 : 0)}</b> a week.
            {perWeek > 15 ? " That's a lot to keep up with." : list.length === 0 ? " Nothing would have reached you." : ""}
          </p>
        </div>
        {list.length > 0 && (
          <ul className="hits">
            {list.slice(0, 8).map((h) => (
              <li key={h.symbol + h.d}>
                <button className="ticker" onClick={() => open(h.symbol)}>{h.symbol}</button>
                <span className="when">{day(h.d)}</span>
                <span className="num hit-figs"><span className={h.m >= 0 ? "up" : "down"}>{signed(h.m)}</span>{h.x !== null ? ` · ${h.x.toFixed(1)}×` : ""}</span>
                {h.note && <p className="hit-note">Filed around then: {h.note}</p>}
              </li>
            ))}
          </ul>
        )}
        <p className="meta">While the market is open and FINN is on your screen, your limits check the latest prices every 5 minutes. This history uses closing prices, and past alerts land in the Chat tab along with filings as they rank.</p>
      </section>

      <PhoneAlerts settings={settings} />

      <section className="sec">
        <h2>Filings that can interrupt you</h2>
        <p className="meta">Turning one off moves it to "Worth knowing". It never disappears.</p>
        <div className="toggles">
          {MUTABLE.map(([id, label]) => {
            const on = !settings.muted.includes(id);
            return (
              <button
                key={id} className={`chip ${on ? "on" : ""}`} aria-pressed={on}
                onClick={() => update({ muted: on ? [...settings.muted, id] : settings.muted.filter((x) => x !== id) })}
              >
                {label}
              </button>
            );
          })}
        </div>
      </section>

      <section className="sec">
        <h2>Promises you're watching</h2>
        {pins.length === 0 ? (
          <p className="meta">Open a company's track record and watch a promise. Filings from that company then rank first, with the promise beside them.</p>
        ) : (
          <ul className="pins">
            {pins.map((p) => (
              <li key={p.symbol + p.claim}>
                <button className="ticker" onClick={() => open(p.symbol)}>{p.symbol}</button>
                <p>{p.claim}</p>
                <p className="meta">{p.period ? `Due ${p.period} · ` : ""}<SourceLink url={p.url}>{fromSource(p.call_label)}, p.{p.page}</SourceLink></p>
                <button
                  className="link"
                  onClick={() => {
                    const next = pins.filter((x) => x !== p);
                    setPins(next);
                    save("finn.pins", next);
                  }}
                >
                  Stop watching
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="sec">
        <h2>How FINN decides</h2>
        <ul className="rules">
          <li><b>Figures come from the filing.</b> Results are the company's own XBRL, calculated by code. A model never does the arithmetic.</li>
          <li><b>Quotes are verbatim.</b> Models point at sentences by number, so they can't invent a quote, and a figure missing from its source gets dropped.</li>
          <li><b>Rank is measured.</b> Each filing type's rank comes from how it moved stocks across {study.filings} filings at {study.companies} companies, compared with quiet days. The few exceptions, such as a top executive leaving, are ranked up on purpose and show their evidence.</li>
          <li><b>Size is relative.</b> A rupee figure is judged against the company's own revenue. The largest order wins moved stocks sharply six times as often as the smallest.</li>
          <li><b>Primary sources only.</b> Research uses filings, transcripts and annual reports, with the company's name hidden from the model.</li>
          <li><b>It can be wrong.</b> Open any source and check. This isn't investment advice.</li>
        </ul>
        {status && (
          <p className="meta num">
            {status.filings.toLocaleString("en-IN")} filings read · {status.llm_calls.toLocaleString("en-IN")} model calls · ${status.llm_usd.toFixed(2)} total
          </p>
        )}
      </section>
    </>
  );
}
