# FINN

**Reads every filing across a portfolio, shows the few that matter, and keeps score of what management promised.**

A mobile-first financial investment agent for Indian equities, built for the ENAM Holdings AI Engineer Build Challenge.

Open it on a phone and try:

1. **Brief:** the filings that matter from the last 24 hours or any dates you pick, ranked by how such filings have actually moved stocks. Tick them off until it says "That's everything."
2. **Chat:** your alerts, and questions answered from FINN's records. Try "Dixon's track record" or "Compare Dixon and Kaynes".
3. **Research:** enter `KAYNES`. FY26 revenue was guided at ₹4,500 crore plus and came in at ₹3,626 crore, with the quote, page and filing one tap away. Then try a ticker of your own.
4. **Watch:** live prices for your holdings. Move the limits and see how many alerts each setting would have sent over the last 30 sessions.

## What makes it different

**Push and pull share one idea.** Research extracts what management committed to. When a new result lands, the brief checks it against what was promised for that quarter.

**It's built so it can't quietly make things up:**

- Models return sentence numbers, not text, so every quote on screen is verbatim.
- A figure that isn't in its cited sentence gets dropped.
- Financial verdicts are calculated by code from the company's filed XBRL.
- The company's name is hidden from analysis models, which enforces the primary-sources rule rather than just requesting it.
- "Can't verify" and "Too vague to test" are real answers, shown as such.
- Every line a model writes is checked against its source, and `eval/check_claims.py` audits them all.

## How it works

```
NSE filings ──> rules decide what's worth opening ──> Haiku reads those PDFs ──> rank against company size ──> Brief
NSE XBRL ─────> code parses exact results ──────────> QoQ / YoY / one-off flags ─────────────────────────────> Results card
Yahoo EOD ────> code computes moves and volume ─────> your limits ───────────────────────────────────────────> Alerts

Transcripts ──> numbered sentences ──> filter to ~10% ──> Haiku picks commitments ──┐
Filed results ──────────────────────────────────────────> code scores the financial ones ──> Track record
Later calls ──────────────────────────> Sonnet judges the rest, a second pass reviews ─┘

All NSE filings + 2 years of prices ──> market study ──> measured rank for each filing type ──> Brief
```

`docs/DECISIONS.md` explains the choices, including the ones real data overturned.

## Run it

Needs Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 22.

```bash
uv sync
(cd web && npm ci && npm run build)
uv run uvicorn finn.api:app --port 8000
```

The repo ships a database with real data, so it works immediately with no keys. Locally, keys go in `finn/.env`, which git ignores. Check the chat's key with `uv run python scripts/check_groq.py`.

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables promise extraction for tickers not already in the database |
| `GEMINI_API_KEY` | Free-tier alternative |
| `FINN_REFRESH_MINUTES` | Keep filings fresh while the server runs (the Dockerfile sets 30) |
| `GROQ_API_KEY` | Runs the Chat tab on `qwen/qwen3.8-27b` (a preview model on Groq; change it with `FINN_GROQ_MODEL`) |
| `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` | Optional. Adds promise tracking on new tickers, and PDF reading on new filings |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CONTACT` | Optional. Turns on phone notifications; make them with `uv run --extra push python scripts/vapid_keys.py` |
| `FINN_CHAT_DAILY_LIMIT` | Chat model calls allowed per day (default 500), since a public link can be asked anything |
| `FINN_BUILD_SLOTS` | New tickers researched at once (default 1, safe on 512 MB) |
| `FINN_LLM_BUDGET_USD` | Hard daily ceiling on model spend (default 3) |

Refresh data with `uv run python -m finn.pipeline refresh`.

**Deploy:** the `Dockerfile` builds a single image. Any Docker host works, such as Hugging Face Spaces (Docker) or Render. Set `PORT` if the host requires it.

## Tests and evaluation

```bash
uv run pytest tests                       # no network, no model
uv run python tests/e2e.py                # real browser; needs the server running
uv run python eval/eval_filings.py holdout -v   # ranker against labelled filings
uv run python eval/reconcile_results.py   # parsed results against screener.in
uv run python eval/check_receipts.py      # every quote against its source PDF page
uv run python eval/check_claims.py        # every model-written line against its source
uv run python eval/event_study.py report  # how each filing type moves stocks; run "fetch" first
FINN_LLM=claude-cli uv run python eval/injection_test.py   # hostile transcripts, real model
uv run python eval/bakeoff_judge.py       # Sonnet vs. Opus on judging promises
FINN_LLM=none uv run python eval/sweep_tickers.py 40   # random tickers, no model cost
```

`docs/RESULTS.md` has the measured numbers and the limits of each one.

## Layout

```
finn/        nse (filings, XBRL)  screener  prices  docs (PDF text)  transcripts
             classify + enrich (the ranker)  promises (track record)  research (dossier)
             llm (routing, cache, spend ledger)  people  chat  pipeline  api
web/         React PWA: Brief, Research, Watch
eval/        how the accuracy claims were measured
tests/       unit, API and browser tests
data/finn.db the snapshot the app ships with
```

FINN reports what filings say. It isn't investment advice, and it can be wrong: every source is one tap away so you can check.
