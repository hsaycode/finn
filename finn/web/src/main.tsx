import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/newsreader/index.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./style.css";
import { BriefScreen } from "./Brief";
import { ChatScreen, alertsPath, type Alert } from "./Chat";
import { ResearchScreen } from "./Research";
import { SummaryScreen } from "./Summary";
import { WatchScreen } from "./Watch";
import { DEFAULT_SETTINGS, api, load, mine, myHoldings, type Settings } from "./lib";

type Tab = "brief" | "chat" | "research" | "watch";
type Screen = Tab | "summary";

const ICONS: Record<Tab, string> = {
  brief: "M5 6h14M5 12h14M5 18h9",
  chat: "M4 5h16v11H9l-5 4z",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  watch: "M3 17l5-6 4 4 8-10M20 5v5h-5",
};
const TABS: [Tab, string, string][] = [["brief", "Brief", "/"], ["chat", "Chat", "/chat"], ["research", "Research", "/research"], ["watch", "Watch", "/watch"]];

function route(): { tab: Screen; symbol: string | null } {
  const [, a, b] = location.pathname.split("/");
  if (a === "summary" && b) return { tab: "summary", symbol: b.toUpperCase() };
  if (a === "research") return { tab: "research", symbol: b ? b.toUpperCase() : null };
  if (a === "watch") return { tab: "watch", symbol: null };
  if (a === "chat") return { tab: "chat", symbol: null };
  return { tab: "brief", symbol: null };
}

function App() {
  const [r, setR] = useState(route);
  const [unread, setUnread] = useState(false);

  useEffect(() => {
    const settings = { ...DEFAULT_SETTINGS, ...load<Partial<Settings>>("finn.settings", {}) };
    api<{ messages: Alert[] }>(alertsPath(settings))
      .then((a) => setUnread(!!a.messages[0] && a.messages[0].ts > load("finn.chat.seen", "")))
      .catch(() => {});
    // A free host forgets what it was following when it restarts, so your own holdings remind it.
    const held = myHoldings();
    if (held) {
      api<{ companies: { symbol: string }[] }>(mine("/api/portfolio")).then((p) => {
        const known = new Set(p.companies.map((c) => c.symbol));
        held.filter((s) => !known.has(s)).slice(0, 5).forEach((s) => api(`/api/track/${s}`, { method: "POST" }).catch(() => {}));
      }).catch(() => {});
    }
  }, []);

  useEffect(() => {
    const onPop = () => setR(route());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  const go = (path: string) => {
    history.pushState(null, "", path);
    setR(route());
    scrollTo(0, 0);
  };

  return (
    <>
      <main className="page">
        {r.tab === "brief" && <BriefScreen open={(s) => go(`/research/${s}`)} goChat={() => go("/chat")} />}
        {r.tab === "summary" && <SummaryScreen symbol={r.symbol!} open={(s) => go(`/research/${s}`)} />}
        {r.tab === "research" && <ResearchScreen symbol={r.symbol} open={(s) => go(s ? `/research/${s}` : "/research")} />}
        {r.tab === "chat" && <ChatScreen open={(s) => go(`/research/${s}`)} onSeen={() => setUnread(false)} />}
        {r.tab === "watch" && <WatchScreen open={(s) => go(`/research/${s}`)} />}
      </main>
      <nav className="tabs" aria-label="Main">
        {TABS.map(([id, label, path]) => (
          <button key={id} className={r.tab === id ? "on" : ""} aria-current={r.tab === id ? "page" : undefined} onClick={() => go(path)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d={ICONS[id]} /></svg>
            <span>{label}{id === "chat" && unread && r.tab !== "chat" && <span className="dot"><span className="sr"> (new alerts)</span></span>}</span>
          </button>
        ))}
      </nav>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
