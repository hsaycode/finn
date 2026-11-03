import { useState, type ReactNode } from "react";
import { VERDICT, type Verdict } from "./lib";

export function VerdictTag({ v }: { v: Verdict }) {
  const m = VERDICT[v];
  return (
    <span className="verdict" style={{ color: m.color }}>
      <span aria-hidden="true">{m.icon}</span> {m.label}
    </span>
  );
}

export function SourceLink({ url, children }: { url: string | null | undefined; children: ReactNode }) {
  if (!url) return <span className="src">{children}</span>;
  return (
    <a className="src" href={url} target="_blank" rel="noopener noreferrer">
      {children} ↗
    </a>
  );
}

/** The line under anything FINN states: where it came from, one tap away. */
export function Cited({ items, label = "Source" }: { items: { label: string; url?: string | null }[]; label?: string }) {
  const shown = items.filter((x) => x.url);
  if (!shown.length) return null;
  return (
    <p className="cited">
      <span className="cited-label">{label}</span>
      {shown.map((x) => <SourceLink key={`${x.label}${x.url}`} url={x.url}>{x.label}</SourceLink>)}
    </p>
  );
}

/** Thin bars from a shared baseline. Tap a bar to read it; the first and last are always labelled. */
export function Bars({ title, labels, values, format, tone }: {
  title: string; labels: string[]; values: (number | null)[]; format: (v: number) => string; tone?: "bad-negative";
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const nums = values.filter((v): v is number => v !== null);
  if (!nums.length) return null;
  const W = 300, H = 74, gap = 2;
  const hi = Math.max(0, ...nums), lo = Math.min(0, ...nums);
  const span = hi - lo || 1;
  const base = (hi / span) * H;
  const bw = (W - gap * (values.length - 1)) / values.length;
  const shown = picked ?? values.length - 1;
  const current = values[shown];
  return (
    <figure className="bars">
      <figcaption>
        <span>{title}</span>
        <span className="num">
          {labels[shown]} · <b>{current === null ? "–" : format(current)}</b>
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${values.map((v, i) => `${labels[i]} ${v === null ? "not available" : format(v)}`).join(", ")}`}>
        {values.map((v, i) => {
          const h = v === null ? 0 : Math.max(1.5, (Math.abs(v) / span) * H);
          const y = v !== null && v < 0 ? base : base - h;
          return (
            <g key={i} onClick={() => setPicked(i)} onMouseEnter={() => setPicked(i)} style={{ cursor: "pointer" }}>
              <rect x={i * (bw + gap)} y={0} width={bw + gap} height={H} fill="transparent" />
              {v !== null && (
                <rect
                  x={i * (bw + gap)} y={y} width={bw} height={h} rx={Math.min(3, bw / 3)}
                  className={`bar ${i === shown ? "bar-on" : ""} ${tone === "bad-negative" && v < 0 ? "bar-neg" : ""}`}
                >
                  <title>{`${labels[i]}: ${format(v)}`}</title>
                </rect>
              )}
            </g>
          );
        })}
        <line x1={0} x2={W} y1={base} y2={base} className="baseline" />
      </svg>
      <div className="bars-axis num"><span>{labels[0]}</span><span>{labels[labels.length - 1]}</span></div>
    </figure>
  );
}

/** Today's path in a table row. Decorative: the row's numbers say the same thing. */
export function Spark({ values, up }: { values: number[]; up: boolean }) {
  if (values.length < 2) return <span className="spark" />;
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 60},${18 - ((v - lo) / span) * 16}`).join(" ");
  return (
    <svg className={`spark ${up ? "up" : "down"}`} viewBox="0 0 60 20" aria-hidden="true">
      <polyline points={pts} />
    </svg>
  );
}

/** One line over time. Hover or tap reads a point, and the caption always holds the figure being read. */
export function PriceChart({ points, label, base }: { points: { d: string; c: number }[]; label: string; base?: number | null }) {
  const [at, setAt] = useState<number | null>(null);
  if (points.length < 2) return null;
  const W = 320, H = 96, pad = 3;
  const values = points.map((p) => p.c);
  const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => pad + (H - pad * 2) * (1 - (v - lo) / span);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join("");
  const shown = at === null ? points.length - 1 : at;
  // Against yesterday's close on the one-day view, so it agrees with the headline figure above it.
  const first = base ?? values[0], current = values[shown];
  const change = (current / first - 1) * 100;
  const pick = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setAt(Math.max(0, Math.min(points.length - 1, Math.round(((e.clientX - box.left) / box.width) * (points.length - 1)))));
  };
  return (
    <figure className="pricechart">
      <figcaption className="num">
        <span>{points[shown].d}</span>
        <span><b>₹{current.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</b> <span className={change >= 0 ? "up" : "down"}>{change >= 0 ? "+" : "−"}{Math.abs(change).toFixed(1)}%</span></span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
        aria-label={`${label}: from ₹${values[0]} on ${points[0].d} to ₹${values[values.length - 1]} on ${points[points.length - 1].d}, between ₹${lo} and ₹${hi}.`}
        onPointerMove={pick} onPointerDown={pick} onPointerLeave={() => setAt(null)}
      >
        <path d={line} className={`priceline ${values[values.length - 1] >= first ? "up" : "down"}`} vectorEffect="non-scaling-stroke" />
        {at !== null && <line x1={x(at)} x2={x(at)} y1={0} y2={H} className="crosshair" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="bars-axis num"><span>₹{lo.toLocaleString("en-IN", { maximumFractionDigits: 0 })} low</span><span>₹{hi.toLocaleString("en-IN", { maximumFractionDigits: 0 })} high</span></div>
    </figure>
  );
}

export function Collapse({ summary, children, defaultOpen = false, className = "" }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapse ${className}`}>
      <button className="collapse-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="collapse-summary">{summary}</span>
        <span className="chev" aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="collapse-body">{children}</div>}
    </div>
  );
}
