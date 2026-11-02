import { useEffect, useState } from "react";
import { api } from "./lib";

export interface CompareData {
  columns: { symbol: string; name: string; format: string | null; quarter: string | null }[];
  rows: { group: string; name: string; values: (number | string | null)[]; unit: string; best: number | null }[];
  missing?: string[];
}

const show = (v: number | string | null, unit: string) => {
  if (v === null || v === undefined) return "–";
  if (typeof v === "string") return v;
  return `${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${unit}`;
};

/** Differently regulated companies report different lines, so mixing them gets a warning rather than a false match. */
const financial = (f: string | null) => f === "banking" || f === "nbfc" || f === "life_insurer" || f === "general_insurer";

export function CompareTable({ table, open }: { table: CompareData; open: (s: string) => void }) {
  const kinds = new Set(table.columns.map((c) => financial(c.format)));
  let group = "";
  return (
    <div className="compare">
      <table>
        <thead>
          <tr>
            <th scope="col"><span className="sr">Measure</span></th>
            {table.columns.map((c) => <th key={c.symbol} scope="col"><button className="ticker" onClick={() => open(c.symbol)}>{c.symbol}</button></th>)}
          </tr>
        </thead>
        <tbody>
          {table.rows.flatMap((r) => {
            const head = r.group !== group ? [<tr key={`g${r.group}`} className="compare-group"><th colSpan={table.columns.length + 1} scope="colgroup">{r.group}</th></tr>] : [];
            group = r.group;
            return [
              ...head,
              <tr key={r.name}>
                <th scope="row">{r.name}</th>
                {r.values.map((v, i) => <td key={i} className={`num ${r.best === i ? "compare-best" : ""}`}>{show(v, r.unit)}{r.best === i && <span className="sr"> (best)</span>}</td>)}
              </tr>,
            ];
          })}
        </tbody>
      </table>
      <p className="meta">Bold marks the better figure where more or less is plainly better. Latest quarters: {table.columns.map((c) => `${c.symbol} ${c.quarter ?? "–"}`).join(", ")}.</p>
      {kinds.size > 1 && <p className="flag">These companies report on different formats, so operating margin, ROCE and debt don't compare like for like.</p>}
    </div>
  );
}

export function CompareSection({ ready, open }: { ready: string[]; open: (s: string) => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [table, setTable] = useState<CompareData | null>(null);

  useEffect(() => {
    if (picked.length < 2) {
      setTable(null);
      return;
    }
    let alive = true;
    api<CompareData>(`/api/compare?symbols=${picked.join(",")}`).then((t) => alive && setTable(t)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [picked.join(",")]);

  const toggle = (s: string) => setPicked((p) => (p.includes(s) ? p.filter((x) => x !== s) : p.length < 3 ? [...p, s] : p));

  return (
    <section className="sec" id="compare">
      <h2>Compare companies</h2>
      <p className="meta">Pick two or three, side by side on filed numbers and promises kept.</p>
      <div className="chips chips-wrap" role="group" aria-label="Companies to compare">
        {ready.map((s) => (
          <button key={s} className={`chip ${picked.includes(s) ? "on" : ""}`} aria-pressed={picked.includes(s)} disabled={!picked.includes(s) && picked.length >= 3} onClick={() => toggle(s)}>{s}</button>
        ))}
      </div>
      {table && <CompareTable table={table} open={open} />}
    </section>
  );
}
