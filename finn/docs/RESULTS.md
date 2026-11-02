# Measured results

What was measured, how, and where each number stops being reliable. Everything here can be rerun with the scripts in `eval/` and `tests/`.

All accuracy reviews below were done by Claude, the AI used to build FINN. No person has audited them.

## Does the brief rank filings correctly?

84 real filings were labelled top, notable or routine by Claude, reading each one without seeing its score. They were then split evenly: one half for tuning, one held out.

| Held-out set: 42 filings, scored once before any fix | Result |
|---|---|
| Routine paperwork correctly buried | 28 of 28 |
| Filings ranked top that deserved attention | 5 of 5 |
| Important filings kept visible | **3 of 4** |

**The miss:** a tribunal admitted an insolvency petition against Polycab over ₹2.79 crore. The size rule judged it trivial. Admission is now serious at any amount, which makes this set read 4 of 4 today, but that later figure isn't a clean test, because the set had already been seen.

**Limits:** the labels are an AI's judgment, not a professional investor's, and Claude had seen some classifier output before labelling. It's a small sample with only 4 truly important filings in the holdout, so treat these as evidence rather than precision. Filings are sampled by category to include rarer types, so they don't reflect the natural mix.

## Are the results figures right?

`eval/reconcile_results.py` compares Revenue (NII for banks), PBT and PAT, parsed from filed XBRL, against screener.in's tables for all 18 holdings over up to 9 quarters.

**459 of 486 figures match within 1.5% (94.4%).** All 27 differences were then traced to the filed tags:

| Differences | What's going on |
|---|---|
| 24 | FINN shows the filed figure and screener defines the line differently. Reliance's revenue, for example, and PBT at Maruti, Asian Paints and Dixon, where screener's figure is higher by what looks like the share of associates (it matches the filed figure in the quarters checked) |
| 1 | L&T, profit before minorities, a field the cards don't show |
| 2 | Adani Enterprises, March 2025. Attributable PAT on the card is ₹3,845 crore, as filed. PBT and the before-minorities line differ from screener by the same amount, and the cause wasn't pinned down |

This tracing also found a real bug, now fixed. HDFC Bank files minority interest under the exceptional-items tag in alternate quarters, which left PBT about 3% low and skewed the comparison. FINN corrects this only when the filing's own segment total confirms it. It accounted for 10 earlier differences, which is why the match rate rose from 92.4%.

## Is every quote really in the source?

`eval/check_receipts.py` opens the PDF behind every quote in every dossier and looks for it on the cited page.

**818 of 818 quotes are verbatim.** 808 are on the cited page, 9 run onto the next page, and the check can't match the last one automatically, because it spans a page break with a footer in the middle. Its two halves were confirmed separately on their pages.

The first run of this check failed, and it was the most useful test in the project. 119 of 679 quotes (18%) couldn't be found as shown, because they joined separate sentences as if they were one passage, sometimes across speakers or across calls. Every sentence was real, but the joined passage wasn't in the PDF, and joining could change the meaning. Quotes now stay with one speaker and one call, and gaps are marked with […].

## Are the promise verdicts right?

The final build has **39 settled verdicts** across 23 dossiers: 3 calculated by code from filed results and 36 made by a model, each backed by a cited later sentence. A second pass overturned 9 further model-made verdicts, which became "can't verify".

| Audit against the surrounding transcript text | Model-made verdicts read | Wrong or unsupported |
|---|---|---|
| First audit | 26 | 6, and 1 arguable |
| After the fixes below, on the companies the fixes were built from | 39 | 1 wrong, 1 with a wrong due date, 2 arguable |
| Six unseen companies, real model, nothing tuned afterwards | 7 | 0 |

The first audit's errors shared a cause: models read sentences without what came before them. FINN judged Dixon's ₹7,000 crore *export* target on a telecom figure, Kaynes' OSAT plant on a sentence about its PCB plant, and Adani's copper promise as Navi Mumbai airport. A code-scored "miss" at Suzlon compared what looks like a contribution margin with EBITDA margin.

**The fixes:** models now see the sentence before each cited one, the second pass sees the text around both promise and evidence, code won't score a margin unless its type is named, a due date can't contradict "over the next few quarters", and a verdict is dropped if its explanation cites a figure nobody can see.

**Limits:**

- The second row isn't a clean test, because the fixes were built from those companies. The clean row has only 7 verdicts, so it shows the fixes didn't obviously overfit, not that the error rate is zero.
- Known flaws remain. Polycab's EHV plant shows the right verdict with a wrong due date (FY28 instead of December 2026), and Dixon's FY26 capex is arguable, since the target appears to include an acquisition.
- Grouping isn't perfect, so a count like Dixon's 9 missed may include one project more than once.
- Most statements stay unresolved: 213 "can't verify", 145 "too vague" and 128 "not due yet", against 39 settled. FINN prefers that to guessing, but the record is thin for companies that give little checkable guidance. TCS, Titan and Nestlé have no settled verdicts at all.

## Does it work on companies nobody prepared?

`eval/sweep_tickers.py` ran two samples of random tickers, 120 in all, drawn from about 1,400 companies that filed in the previous week. These runs used no model, so they test data handling and fallbacks.

| Of 120 companies | |
|---|---|
| Built without a crash | 120 |
| Filed results available | 112 |
| Trajectory, balance sheet and cash flow available | 120 |
| At least one readable transcript | 85 |

Six more unseen companies ran with the real model, at $0.04 to $0.20 each. One exposed a gap, now fixed: Trident's newest readable call is from FY22, and the dossier didn't say so.

**Limits:** earlier runs found real crashes (Nestlé), so clean samples don't prove there are no others. Some companies publish no usable transcript: they don't file them, or they file a letter pointing to a website.

## What happens when something fails?

A copy built by following the Dockerfile's steps, with production dependencies and no PDF cache, was run under each failure. Shipped data kept working in all of them.

| Failure | What the investor sees |
|---|---|
| All sources blocked | "Couldn't reach its data sources", not "check the ticker". Ask says it couldn't open the transcripts, not that they don't mention the topic |
| Only NSE blocked, as a cloud host might be | A partial dossier that says NSE couldn't be reached, and retries later |
| Wrong API key | Financial sections still build, with a note that the model failed |
| Daily spending limit reached | The same, with a note saying no model was available |
| Filings refresh reaches nothing | "Filings checked" keeps its last true time and names the companies it couldn't reach |

Earlier versions gave false reasons in the first and last of these.

**Hostile documents:** `eval/injection_test.py` plants instructions inside transcript sentences and runs the real model. All 10 checks passed: planted claims weren't extracted, planted sentences weren't cited, "mark it delivered" didn't change a verdict, and Ask didn't repeat planted advice. It's a handful of attacks, not proof of safety. The real protection is structural: models have no tools, and code checks what they return.

**Size:** building a large bank's dossier peaked at 230 MB after a fix (463 MB before), measured on Windows, so a 512 MB host should fit.

## Which model for judgment?

`eval/bakeoff_judge.py` ran Sonnet and Opus over identical evidence for three companies. They agreed on 69 of 79 verdicts (87%). Neither was clearly better on the other 10, and Sonnet cost about 40% as much. There's no ground truth for those 10, so this is a judgment call.

## Cost

| | |
|---|---|
| Reading a filing | about $0.0012 |
| A new company's dossier | about $0.04 to $0.20, depending on how much it promises |
| Everything spent building this, including rework, experiments and testing | about $8.35 across 1,197 model calls |

Figures come from the app's own ledger of model calls, as reported by the provider. Repeat runs on unchanged documents cost nothing because answers are cached. Spending has a daily ceiling.

## Software tests

116 automated tests (`pytest tests`, with no network or model) and 14 browser checks at phone size (`tests/e2e.py`), run in Chromium, WebKit (Safari's engine) and Firefox. An accessibility scan and phone-size checks ran from 320 to 768 pixels wide. Many of the tests pin specific bugs found while building.

**Not tested:** real calls to the Anthropic API (a local stub and the Claude CLI only), Gemini, Telegram against the live service, the actual Docker build, whether NSE accepts requests from a cloud host, and use by real investors.
