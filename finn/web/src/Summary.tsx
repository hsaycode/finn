import { useEffect, useState } from "react";
import { METRIC_NAMES, api, fromSource, signed, yahooUrl, type Metric, type Promise_ } from "./lib";
import { SourceLink, VerdictTag } from "./ui";

const value = (v: number | null) => (v === null ? "–" : `${v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);

function Said({ p }: { p: Promise_ }) {
  return (
    <li>
      <p><VerdictTag v={p.verdict} /> {p.claim}{p.actual ? <b className="num"> Actual {p.actual}.</b> : null}</p>
      <figure className="quote">
        <blockquote>{p.quote}</blockquote>
        <figcaption>{p.speaker || "Management"}, {fromSource(p.call_label)} · <SourceLink url={p.url}>page {p.page}</SourceLink></figcaption>
      </figure>
      {p.note && <p className="meta">{p.note}</p>}
    </li>
  );
}

/** One page to read, print or send. Every line here is already in the dossier, so nothing new is generated. */
export function SummaryScreen({ symbol, open }: { symbol: string; open: (s: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [state, setState] = useState("loading");
  const [quote, setQuote] = useState<any>(null);
  const [shared, setShared] = useState("");

  useEffect(() => {
    api<any>(`/api/research/${symbol}`).then((r) => {
      setD(r.dossier ?? null);
      setState(r.dossier ? "ready" : "missing");
    }).catch(() => setState("failed"));
    api<any>(`/api/quote/${symbol}`).then(setQuote).catch(() => {});
  }, [symbol]);

  if (state === "loading") return <div className="skeleton skeleton-card" aria-hidden="true" />;
  if (!d) {
    return (
      <div className="empty">
        <h1 className="dossier-name">{symbol}</h1>
        <p>{state === "failed" ? "Couldn't reach FINN. Check your connection." : "FINN hasn't researched this company yet."}</p>
        <button className="primary" onClick={() => open(symbol)}>Open research</button>
      </div>
    );
  }

  const pr = d.promises;
  const ps: Promise_[] = pr?.promises ?? [];
  const failed = ps.filter((p) => p.verdict === "missed" || p.verdict === "partly").slice(0, 3);
  const kept = ps.filter((p) => p.verdict === "delivered").slice(0, 2);
  const due = ps.filter((p) => p.verdict === "not_due" && p.period).sort((a, b) => a.period!.localeCompare(b.period!)).slice(0, 4);
  const q = d.snapshot?.quarter;
  const record = pr?.testable ? `kept ${pr.tally.delivered} of ${pr.testable} promises FINN could check` : "";
  const share = async () => {
    const title = `${d.name} on FINN`;
    try {
      if (navigator.share) {
        await navigator.share({ title, text: record ? `${d.name} ${record}.` : title, url: location.href });
      } else {
        await navigator.clipboard.writeText(location.href);
        setShared("Link copied.");
      }
    } catch {
      /* closing the share sheet isn't an error */
    }
  };

  return (
    <article className="summary">
      <header className="dossier-head">
        <div className="no-print summary-actions">
          <button className="link back" onClick={() => open(symbol)}>← Full research</button>
          <span>
            <button className="chip" onClick={() => print()}>Print or save as PDF</button>
            <button className="chip" onClick={share}>Share</button>
          </span>
        </div>
        {shared && <p className="meta no-print" role="status">{shared}</p>}
        <h1 className="dossier-name">{d.name}</h1>
        <p className="meta">{d.symbol}{d.business?.industry ? ` · ${d.business.industry}` : ""} · built {new Date(d.generated).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</p>
        {quote?.price != null && (
          <p className="meta num">₹{quote.price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}{quote.change_pct != null ? ` · ${signed(quote.change_pct, 2)}` : ""} · {quote.as_of} · <SourceLink url={yahooUrl(d.symbol)}>Yahoo Finance</SourceLink></p>
        )}
      </header>

      <section className="sec" aria-label="Track record">
        <h2>Did they do what they said?</h2>
        {pr?.testable > 0
          ? <p className="score">Kept <b className="num">{pr.tally.delivered}</b> of <b className="num">{pr.testable}</b> promises FINN could check. <span className="meta">Fell short on {pr.tally.partly}, missed {pr.tally.missed}.</span></p>
          : <p>{pr ? "No promise has reached its due date with evidence FINN could check." : "No promise record yet."}</p>}
        {failed.length > 0 && <ul className="summary-list">{failed.map((p, i) => <Said key={i} p={p} />)}</ul>}
        {kept.length > 0 && <ul className="summary-list">{kept.map((p, i) => <Said key={i} p={p} />)}</ul>}
        {pr?.moves?.length > 0 && (
          <>
            <h3 className="eyebrow">Moved the goalposts</h3>
            {pr.moves.slice(0, 3).map((m: any, i: number) => (
              <p key={i} className="move">
                <b>{m.metric.replaceAll("_", " ")}, {m.period}:</b> <span className="num">{m.from}{m.unit === "%" ? "%" : ""} → {m.to}{m.unit === "%" ? "%" : ""}</span> between the {m.from_call} and {m.to_call} <SourceLink url={m.to_url}>Source</SourceLink>
              </p>
            ))}
          </>
        )}
      </section>

      {q && (
        <section className="sec" aria-label="Latest results">
          <h2>Latest results</h2>
          <table className="results">
            <caption>{q.label} · {q.basis} · <SourceLink url={q.source}>{q.source_label ?? "Filed results (XBRL)"}</SourceLink></caption>
            <thead><tr><th scope="col">₹ crore</th><th scope="col">{q.label}</th><th scope="col">QoQ</th><th scope="col">YoY</th></tr></thead>
            <tbody>
              {Object.entries<Metric>(q.metrics).map(([k, m]) => (
                <tr key={k}>
                  <th scope="row">{METRIC_NAMES[k] ?? k}</th>
                  <td className="num">{value(m.value)}</td>
                  <td className="num">{signed(m.qoq)}</td>
                  <td className="num">{signed(m.yoy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {d.arguments && (
        <section className="sec" aria-label="Bull and bear">
          <h2>Bull vs. bear</h2>
          <h3 className="sub-h">Bull</h3>
          <ul className="summary-points">{d.arguments.bull.map((x: any, i: number) => <li key={i}>{x.point} {x.sources[0] && <SourceLink url={x.sources[0].url ?? d.sources?.screener}>{x.sources[0].source}</SourceLink>}</li>)}</ul>
          <h3 className="sub-h">Bear</h3>
          <ul className="summary-points">{d.arguments.bear.map((x: any, i: number) => <li key={i}>{x.point} {x.sources[0] && <SourceLink url={x.sources[0].url ?? d.sources?.screener}>{x.sources[0].source}</SourceLink>}</li>)}</ul>
          {d.arguments.swing && <p className="swing"><span className="eyebrow">What decides it</span>{d.arguments.swing}</p>}
        </section>
      )}

      {due.length > 0 && (
        <section className="sec" aria-label="Next due">
          <h2>What to watch next</h2>
          <ul className="summary-points">{due.map((p, i) => <li key={i}><b className="num">{p.period}:</b> {p.claim} <SourceLink url={p.url}>{p.call_label}, p.{p.page}</SourceLink></li>)}</ul>
        </section>
      )}

      {d.missing?.map((m: string) => <p key={m} className="flag">{m}</p>)}
      {d.built_by === "groq" && <p className="meta">Read by a free model, which FINN has checked less than its other companies. Open the sources before relying on a verdict.</p>}
      <p className="asof">From filed results, earnings calls and annual reports, with multi-year figures from screener.in. This isn't investment advice. Open any source and check.</p>
    </article>
  );
}
