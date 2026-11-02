import { useEffect, useRef, useState, type FormEvent } from "react";
import { CompareTable } from "./Compare";
import { DEFAULT_SETTINGS, ago, api, load, save, type Settings } from "./lib";
import { SourceLink } from "./ui";

interface Item { symbol: string | null; title: string; detail: string | null; url: string | null; when: string | null }
interface Reply { text: string; items?: Item[]; suggest?: string[]; table?: any; quotes?: { text: string; speaker: string; page: number; url: string; call_label: string }[] }
interface Message extends Reply { from: "you" | "finn"; at: string }
export interface Alert { kind: "filing" | "move"; ts: string; symbol: string; title: string; text: string; url: string | null; label: string; tone?: string; move?: number }

const STARTERS = ["What matters today?", "What moved?", "Dixon's track record", "Compare Dixon and Kaynes"];
const KEPT = 30;

export const alertsPath = (s: Settings) => `/api/alerts?move=${s.movePct}&vol=${s.volX}&muted=${encodeURIComponent(s.muted.join(","))}`;

function AlertBubble({ a, open }: { a: Alert; open: (s: string) => void }) {
  return (
    <article className={`bubble bubble-alert ${a.kind === "move" ? "bubble-move" : ""}`}>
      <header className="bubble-head">
        <button className="ticker" onClick={() => open(a.symbol)}>{a.symbol}</button>
        <span className="kind">{a.label}</span>
        <span className="when num">{ago(a.ts)}</span>
      </header>
      <p className={`bubble-title ${a.kind === "move" ? `num ${(a.move ?? 0) >= 0 ? "up" : "down"}` : ""}`}>{a.title}</p>
      <p className="meta">{a.text}{a.url ? " " : ""}{a.url && <SourceLink url={a.url}>Filing</SourceLink>}</p>
    </article>
  );
}

function Answer({ m, open, send }: { m: Message; open: (s: string) => void; send: (t: string) => void }) {
  return (
    <div className="bubble bubble-finn">
      <p>{m.text}</p>
      {m.table?.columns?.length > 0 && <CompareTable table={m.table} open={open} />}
      {m.items && m.items.length > 0 && (
        <ul className="answer-items">
          {m.items.map((x, i) => (
            <li key={i}>
              {x.symbol && x.url ? <button className="ticker" onClick={() => open(x.symbol!)}>{x.symbol}</button> : null}
              {x.symbol && !x.url
                ? <button className="link" onClick={() => open(x.symbol!)}>{x.title}</button>
                : x.url && !x.symbol ? <SourceLink url={x.url}>{x.title}</SourceLink> : <span>{x.title}</span>}
              {x.detail && <span className="meta answer-detail">{x.detail}</span>}
              {x.symbol && x.url && <SourceLink url={x.url}>Filing</SourceLink>}
            </li>
          ))}
        </ul>
      )}
      {m.quotes?.map((q, i) => (
        <figure className="quote" key={i}>
          <blockquote>{q.text}</blockquote>
          <figcaption>{q.speaker || "Management"}, {q.call_label} call · <SourceLink url={q.url}>page {q.page}</SourceLink></figcaption>
        </figure>
      ))}
      {m.suggest && m.suggest.length > 0 && (
        <div className="chips chips-wrap">{m.suggest.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>
      )}
    </div>
  );
}

export function ChatScreen({ open, onSeen }: { open: (s: string) => void; onSeen: () => void }) {
  const settings: Settings = { ...DEFAULT_SETTINGS, ...load<Partial<Settings>>("finn.settings", {}) };
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [messages, setMessages] = useState<Message[]>(() => load("finn.chat", []));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ messages: Alert[] }>(alertsPath(settings)).then((r) => {
      setAlerts(r.messages);
      if (r.messages[0]) save("finn.chat.seen", r.messages[0].ts);
      onSeen();
    }).catch(() => {});
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, busy, alerts.length]);

  const send = async (raw: string) => {
    const message = raw.trim();
    if (!message || busy) return;
    const mine: Message = { from: "you", text: message, at: new Date().toISOString() };
    const next = [...messages, mine];
    setMessages(next);
    setText("");
    setBusy(true);
    let reply: Reply;
    try {
      reply = await api<Reply>("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, move: settings.movePct, vol: settings.volX }),
      });
    } catch {
      reply = { text: "I couldn't reach FINN just now. Check your connection and try again." };
    }
    const all: Message[] = [...next, { ...reply, from: "finn", at: new Date().toISOString() }];
    setMessages(all);
    save("finn.chat", all.slice(-KEPT));
    setBusy(false);
  };

  const shown = showAll ? alerts : alerts.slice(0, 6);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(text);
  };

  return (
    <div className="chat">
      <header className="mast">
        <h1 className="lede">Ask FINN.</h1>
        <p className="moment">Alerts land here. Answers come only from filings, filed results, prices and earnings calls, and FINN says when it doesn't know.</p>
      </header>

      <section aria-label="Alerts">
        <h2 className="eyebrow">Alerts, newest first{alerts.length ? ` · ${alerts.length}` : ""}</h2>
        {alerts.length === 0 && <p className="meta">Nothing has crossed your limits or ranked as important in the last 14 days.</p>}
        {shown.map((a) => <AlertBubble key={`${a.symbol}${a.ts}${a.kind}${a.title}`} a={a} open={open} />)}
        {alerts.length > shown.length && <button className="link widen" onClick={() => setShowAll(true)}>Show {alerts.length - shown.length} earlier alerts</button>}
      </section>

      <section aria-label="Conversation" className="thread-chat" aria-live="polite">
        <h2 className="eyebrow">Your questions</h2>
        {messages.length === 0 && (
          <>
            <p className="meta">Try one of these.</p>
            <div className="chips chips-wrap">{STARTERS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>
          </>
        )}
        {messages.map((m, i) => (m.from === "you"
          ? <p key={i} className="bubble bubble-you">{m.text}</p>
          : <Answer key={i} m={m} open={open} send={send} />))}
        {busy && <div className="bubble bubble-finn"><div className="pulse" aria-hidden="true" /><p className="meta">Reading…</p></div>}
        {messages.length > 0 && !busy && (
          <button className="link widen" onClick={() => { setMessages([]); save("finn.chat", []); }}>Clear conversation</button>
        )}
        <div ref={bottom} />
      </section>

      <form className="composer" onSubmit={submit}>
        <label className="sr" htmlFor="chat-input">Ask FINN</label>
        <input id="chat-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={400} placeholder="Ask about a company" autoComplete="off" />
        <button className="primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  );
}
