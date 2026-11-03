# Measured results

What was measured, how, and where each number stops being reliable. Everything here can be rerun with the scripts in `eval/` and `tests/`.

All accuracy reviews below were done by Claude, the AI used to build FINN. No person has audited them.

## How much does each kind of filing matter?

`eval/event_study.py` measures it. It takes every filing on NSE over 18 months, **280,486 filings at 2,284 companies across 492 trading days**, and asks whether the stock moved sharply on the first session that could react. A sharp move is more than 2 of the stock's own recent standard deviations, after removing the market's move. Each filing type is compared with the same stocks on days they filed nothing, when a sharp move happens 5.4% of the time.

The hand-set scores FINN started with only loosely tracked this (rank correlation 0.31 across 81 filing types), so they were replaced. Filings within a session of results are excluded, so they can't borrow the results' reaction.

| Filing type | Sharp move | Against a quiet day | Filings |
|---|---|---|---|
| Offer for sale | 35.8% | 6.7× | 53 |
| Quarterly results | 27.7% | 5.2× | 13,133 |
| Order wins | 11.7% | 2.2× | 2,042 |
| A top executive leaving | 8.8% | 1.6× | 1,053 |
| Merger or scheme | 10.5% | 2.0× | 600 |
| Insolvency or tribunal filings | 4.7% | 0.9× | 869 |
| Newspaper ad copies | 5.8% | 1.1× | 17,867 |

**The test that counts:** ranks learned from the first year only, judged on the 73,367 filings of the last 26 weeks, which they never saw.

| Tier | Old hand-set rules | Measured ranks |
|---|---|---|
| Top | 4.67× a quiet day | 5.00× |
| Notable | 1.34× | 1.56× |
| Routine | 1.15× | 1.10× |

Routine got quieter and notable got sharper. Leaving out results, the top tier reacts *less* than before (1.47× against 1.66×), because top-executive changes stay there on purpose.

**Size matters too.** Among order wins with an amount in the filing's text, the largest third moved stocks sharply 18.5% of the time and the smallest third 3.1%, though that's only 195 filings.

**Where the data doesn't decide,** and why (each is recorded in `finn/calibration.json`):

- *Raised on purpose:* top-executive changes, which the brief names as material, and defaults, closures, strikes and pledges, where cases are too few to measure and the event is serious if true.
- *Left to the PDF:* deals, tax orders and tribunal filings. Most are minor, so their type alone doesn't rank them, and their size or content decides.
- *Kept low:* recording links and investor decks, because the call or results moved the stock, not the document.
- *Not measured:* exchange queries about price moves, because the move causes the query.
- *Capped:* dividends, which measure high but are often decided alongside results.

**Limits:** a price move shows that investors reacted. It doesn't prove the filing caused the move, and it can't see what matters slowly, such as a governance problem. It covers all listed companies, where large companies react a little differently. Ranks that come from reading a filing's PDF, such as a rating cut, were set by judgment, because reading 280,000 PDFs wasn't practical.

## Does the brief rank this portfolio's filings sensibly?

84 real filings were labelled top, notable or routine by Claude, reading each one without seeing its score, then split evenly into a tuning half and a held-out half.

| Held-out set: 42 filings | First run, before any fix | Now, with measured ranks |
|---|---|---|
| Routine paperwork correctly buried | 28 of 28 | 27 of 28 |
| Filings ranked top that deserved attention | 5 of 5 | 6 of 6 |
| Important filings kept visible | 3 of 4 | 4 of 4 |

Only the first column is a clean test, because the set had since been seen. Its one miss was an insolvency admission over ₹2.79 crore that a size rule judged trivial, so admission now ranks as serious at any amount. These labels are an AI's judgment on a small sample, so treat them as a sanity check. The market study above is the stronger evidence.

## Are the results figures right?

`eval/reconcile_results.py` compares Revenue (NII for lenders), PBT and PAT, parsed from filed XBRL, against screener.in's tables for all 18 holdings over up to 9 quarters.

**459 of 486 figures match within 1.5% (94.4%).** All 27 differences were traced to the filed tags:

| Differences | What's going on |
|---|---|
| 24 | FINN shows the filed figure and screener defines the line differently. Reliance's revenue, for example, and PBT at Maruti, Asian Paints and Dixon, where screener's figure is higher by what looks like the share of associates (it matches the filed figure in the quarters checked) |
| 1 | L&T, profit before minorities, a field the cards don't show |
| 2 | Adani Enterprises, March 2025. Attributable PAT on the card is ₹3,845 crore, as filed. PBT and the before-minorities line differ from screener by the same amount, and the cause wasn't pinned down |

The tracing found a real bug, now fixed: HDFC Bank files minority interest under the exceptional-items tag in alternate quarters, leaving PBT about 3% low. FINN corrects this only when the filing's own segment total confirms it.

**Other formats:** NBFCs were misread as banks and insurers use a different taxonomy, so their headline figures came out blank. Both now parse correctly, checked on Bajaj Finance, Muthoot, SBI Life, HDFC Life and ICICI Lombard. Star Health's newer filing format gives profit and ratios but no premium figures. NBFC pre-provision profit is calculated from filed lines, and the card says so.

## Is every quote really in the source?

`eval/check_receipts.py` opens the document behind every quote and looks for it on the cited page.

**1,067 of 1,067 quotes are verbatim.** 1,057 are on the cited page, 9 run onto the next page, and the check can't match the last automatically because it spans a page break with a footer in the middle. Its two halves were confirmed separately.

The first run of this check failed, and it was the most useful test in the project: 119 of 679 quotes (18%) joined separate sentences as if they were one passage, sometimes across speakers or calls. Quotes now stay with one speaker and one call, and gaps are marked with […].

## Is anything model-written unsupported?

`eval/check_claims.py` checks every line a model wrote against the text beside it. A line fails if a figure in it, or for people a name, isn't in its source. The same rules now run when each line is written, so a failing line isn't shown.

| What | Checked | Failed |
|---|---|---|
| Filing headlines | 675 | 0 |
| Promise summaries | 503 | 0 |
| Filing details | 186 | 0 |
| Bull and bear points | 137 | 0 |
| Company KPIs | 122 | 0 |
| A person's background | 111 | 0 |
| Annual report notes | 41 | 2 |
| Verdict notes | 37 | 0 |
| Business descriptions | 22 | 0 |

The last full run caught 2 annual report notes whose figures weren't on the cited page. Those two companies' dossiers were then rebuilt under a stricter rule that hides such notes, but this table wasn't rerun afterwards.

**Limits:** a pass means no figure or name was invented. It can't prove a summary's meaning is fair, which is why sources stay one tap away.

## Are the promise verdicts right?

The final build has **40 settled verdicts** across the 12 of 23 dossiers that have any: 3 calculated by code from filed results and 37 made by a model, each backed by a cited later sentence. A second pass overturned 5 further model-made verdicts, which became "can't verify".

| Audit against the surrounding transcript text | Verdicts read | Wrong or unsupported |
|---|---|---|
| First audit | 26 model-made | 6, and 1 arguable |
| After the fixes below, on the companies they were built from | 39 model-made | 1 wrong, 1 with a wrong due date, 2 arguable |
| Six unseen companies, real model, nothing tuned afterwards | 7 model-made | 0 |
| Verdicts that changed in the final rebuild | 8 model-made | 2 doubtful, 1 since fixed by a code rule |

The first audit's errors shared a cause: models read sentences without what came before them. FINN judged Dixon's ₹7,000 crore *export* target on a telecom figure, and Adani's copper promise as Navi Mumbai airport. A code-scored "miss" at Suzlon compared what looks like a contribution margin with EBITDA margin.

**The fixes:** models now see the sentence before each cited one, and the second pass sees the text around both promise and evidence. Code won't score a margin unless its type is named. A due date can't contradict "over the next few quarters". A verdict is dropped if its explanation cites a figure nobody can see. "Delivered" fails if the promise said "fully" or "commercial production" and the evidence doesn't.

**Limits:**

- Only the unseen-companies row is a clean test, and 7 verdicts is too few to show the error rate is low. Expect some wrong verdicts to remain.
- Known flaws: Polycab's EHV plant shows the right verdict with a wrong due date (FY28 instead of December 2026), Dixon's FY26 capex is arguable, and one Dixon verdict may mix up two plants.
- Grouping isn't perfect, so Dixon's 10 missed may count one project more than once.
- Re-judging can change a verdict's wording or outcome, because models aren't fully repeatable. Dixon has read 2 of 14, 3 of 14 and now 3 of 15 across rebuilds.
- Most statements stay unresolved: 193 "can't verify", 150 "too vague" and 120 "not due yet", against 40 settled. TCS, Titan, Reliance and HDFC Bank have no settled verdicts.

## Does it work on companies nobody prepared?

`eval/sweep_tickers.py` ran three random samples, 200 runs in all, drawn from about 1,400 to 1,500 companies that filed in the previous week. They used no model, so they test data handling and fallbacks. **None crashed.**

| Latest sample, 80 companies | |
|---|---|
| Has earnings-call transcripts | 56 |
| Only annual reports | 17 |
| Neither | 7 |
| Filed results available | 78 |

**Why not every company?** Of 26 with no readable call in an earlier sample, about 20 showed no sign of holding investor calls: no transcript links and no call-related filings in 500 days. So FINN builds their record from annual reports and says so. Some companies file a letter linking to the transcript, which FINN now follows with safeguards: https only, public addresses only, PDFs only, size capped. Maruti and Titagarh stay at one call each: Maruti's letters point to a web page rather than a PDF, and Titagarh's website has a broken security certificate, which FINN won't bypass.

Six more unseen companies ran with the real model, at $0.04 to $0.20 each.

**Limits:** samples can't prove there are no crashes. Earlier runs found real ones. Annual-report records are new and thin: JP Power's produced only 2 statements, both too vague to test.

## What happens when something fails?

A copy built by following the Dockerfile's steps, with production dependencies and no PDF cache, was run under each failure. Shipped data kept working in all of them.

| Failure | What the investor sees |
|---|---|
| All sources blocked | "Couldn't reach its data sources", not "check the ticker". Ask says it couldn't open the transcripts |
| Only NSE blocked, as a cloud host might be | A partial dossier that says NSE couldn't be reached, and retries later |
| Wrong API key | Financial sections still build, with a note that the model failed. Chat shows the closest passages, and Groq's real rejection of a fake key was tested |
| Daily spending limit reached | The same, with a note saying no model was available |
| Filings refresh reaches nothing | "Filings checked" keeps its last true time and names the companies it couldn't reach |

**Hostile documents:** `eval/injection_test.py` plants instructions inside transcript sentences and runs the real model. All 10 checks passed. It's a handful of attacks, not proof of safety. The real protection is structural: models have no tools, and code checks what they return.

**Size:** building a large bank's dossier peaked at 230 MB after a fix (463 MB before), measured on Windows, so a 512 MB host should fit. New companies build one at a time.

## Which model for judgment?

`eval/bakeoff_judge.py` ran Sonnet and Opus over identical evidence for three companies. They agreed on 69 of 79 verdicts (87%). Neither was clearly better on the other 10, and Sonnet cost about 40% as much. There's no ground truth for those 10, so this is a judgment call.

## Cost

| | |
|---|---|
| Reading a filing | about $0.0014 |
| A new company's dossier | about $0.04 to $0.20, depending on how much it promises |
| Everything spent building this, including rework, experiments and testing | about $10.60 across 1,898 model calls |

Figures come from the app's own ledger, as reported by the provider. Repeat runs on unchanged documents cost nothing because answers are cached. Spending has a daily ceiling, and chat has its own daily limit.

## Software tests

158 automated tests (`pytest tests`, with no network or model) and 22 browser checks at phone size (`tests/e2e.py`), run in Chromium, WebKit (Safari's engine) and Firefox. An accessibility scan found no serious issues in either theme, and phone-size checks ran from 320 to 768 pixels wide. Some inline source links are under 24 pixels tall, and a dossier takes about 5 seconds to open on simulated slow 4G. One Firefox run failed a click on the Brief, which took 3 dependent checks with it, and the next run passed in full. The cause wasn't found, so that may be an intermittent problem.

**Not tested:** phone notifications on a real phone (they're off by default, and the sending logic and its safeguards have unit tests only), Qwen on Groq with a real key, real calls to the Anthropic API (a local stub and the Claude CLI only), Gemini, the actual Docker build, whether NSE accepts requests from a cloud host, and use by real investors.
