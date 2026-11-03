export type Verdict = "delivered" | "partly" | "missed" | "not_due" | "no_evidence" | "vague";

export interface Promise_ {
  claim: string; kind: string; metric: string; low: number | null; high: number | null; unit: string | null;
  period: string | null; basis: string | null; call_label: string; said_period: string; quote: string; speaker: string;
  page: number; url: string; verdict: Verdict; note: string; actual?: string; checked_by?: string; scope?: string;
  reaffirmed?: string[]; restated?: string[]; deadline_moves?: string[]; went_quiet?: boolean; overturned?: { verdict: string; note: string };
  evidence?: { quote: string; page: number; url: string; call_label: string; speaker: string };
}

export interface Person {
  name: string; role: string; action: string; effective?: string | null; reason?: string | null; profile?: string[]; top_executive?: boolean;
  age?: number; qualifications?: string[]; career?: string[]; other_boards?: string[]; time_at_company?: string; replaces?: string; term?: string;
  source?: { label: string; url: string; page: number };
  context?: { top_changes_12m?: number; on_calls?: { spoke: number; of: number }; reaction?: { date: string; move_pct: number; vol_x: number | null } };
}

export interface Metric { value: number | null; qoq: number | null; yoy: number | null; prev: number | null; year_ago: number | null }

export interface ResultsCard {
  label: string; period_end: string; basis: string; audited: boolean; banking: boolean; source: string; source_label?: string;
  metrics: Record<string, Metric>; margin?: number | null; gross_npa?: number | null; net_npa?: number | null;
  format?: string; sector_stats?: { label: string; value: number; unit: string }[]; built?: string | null;
  flags: { kind: string; text: string }[];
}

export interface Filing { id: string; ts: string; title: string; pdf_url: string | null; reason: string; score: number }

export interface Event {
  id: string; symbol: string; name: string; ts: string; category: string; category_label: string; score: number;
  tier: "top" | "notable" | "routine"; title: string; reason: string; pdf_url: string | null; filings: Filing[];
  detail: {
    tone?: string; amount_crore?: number; amount_note?: string; funding_type?: string; response?: string; read_by?: string;
    person?: Person;
    rating?: { agency: string; action: string; rating: string; outlook?: string | null };
    legal?: { authority: string; nature: string; company_view?: string | null };
  };
  evidence?: { big_move_pct: number; quiet_pct: number; lift: number; n: number; volume_x: number } | null;
  results?: ResultsCard | null;
  promise_check?: { checked: Promise_[]; upcoming: Promise_[] } | null;
}

export interface Brief {
  top: Event[]; notable: Event[]; routine: Event[]; total_filings: number; latest_notable: Event | null;
  meta: { last_refresh: string | null; window_start: string; unreached?: string[]; oldest_filing?: string | null; newest_filing?: string | null; gap?: { from: string; to: string } | null };
}

export interface Session { d: string; c: number; m: number; x: number | null; ok: boolean }
export interface Holding { symbol: string; name: string; banking: boolean; filings: { d: string; title: string; category: string; url?: string | null }[]; last: { date: string; close: number; move_pct: number; vol_x: number | null } | null; series: Session[] }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export const inr = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: digits });

export const cr = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "–";
  const neg = v < 0;
  return `${neg ? "−" : ""}₹${inr(Math.abs(v))} cr`;
};

export const signed = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;

export function ago(ts: string): string {
  const d = new Date(ts);
  const mins = (Date.now() - d.getTime()) / 60000;
  if (mins < 60) return `${Math.max(1, Math.round(mins))}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: settings just won't persist */
  }
}

export const MAX_HOLDINGS = 30;
export const myHoldings = () => load<string[] | null>("finn.holdings", null);
/** Asks for your own holdings once you've changed them. They stay on your device, because FINN has no accounts. */
export function mine(path: string) {
  const h = myHoldings();
  return h?.length ? `${path}${path.includes("?") ? "&" : "?"}symbols=${encodeURIComponent(h.join(","))}` : path;
}

export interface Settings { volX: number; movePct: number; muted: string[] }
export const DEFAULT_SETTINGS: Settings = { volX: 2, movePct: 5, muted: [] };

export interface Pin { symbol: string; claim: string; period: string | null; call_label: string; url: string; page: number; quote: string }

/** Where the Indian market is right now, which decides what the brief can honestly say. */
export function marketMoment(now = new Date()): { label: string; note: string } {
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  const mins = ist.getHours() * 60 + ist.getMinutes();
  const weekday = ist.getDay();
  if (weekday === 0 || weekday === 6) return { label: "Market closed", note: "Weekend" };
  if (mins < 555) return { label: "Before the open", note: "Opens 9:15" };
  if (mins < 930) return { label: "Market open", note: "Your limits check live prices" };
  return { label: "After the close", note: "Today's prices are final" };
}

export const METRIC_NAMES: Record<string, string> = {
  revenue: "Revenue", ebitda: "EBITDA", pbt: "PBT", pat: "PAT", nii: "Net interest income", ppop: "Pre-provision profit",
  gross_premium: "Gross premium", net_premium: "Net premium", underwriting: "Underwriting result",
};

export const fromSource = (label: string) => (label.includes("annual report") ? label : `${label} call`);

/** Where a reader can check what FINN says. */
export const yahooUrl = (symbol: string) => `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}.NS`;
export const nseUrl = (symbol: string) => `https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(symbol)}`;
export const METHOD_URL = "https://github.com/hsaycode/finn/blob/main/finn/docs/RESULTS.md#how-much-does-each-kind-of-filing-matter";
export const FILINGS_URL = "https://www.nseindia.com/companies-listing/corporate-filings-announcements";

export const VERDICT: Record<Verdict, { label: string; icon: string; color: string }> = {
  delivered: { label: "Delivered", icon: "✓", color: "var(--good)" },
  partly: { label: "Fell short", icon: "≈", color: "var(--warn-ink)" },
  missed: { label: "Missed", icon: "✕", color: "var(--bad)" },
  not_due: { label: "Open", icon: "○", color: "var(--open)" },
  no_evidence: { label: "Can't verify", icon: "?", color: "var(--ink3)" },
  vague: { label: "Too vague", icon: "~", color: "var(--ink3)" },
};
