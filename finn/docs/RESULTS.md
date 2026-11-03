# Measured results

What was measured, how, and where each number stops being reliable. Everything here can be rerun with the scripts in `eval/` and `tests/`.

All accuracy reviews below were done by Claude, the AI used to build FINN. No person has audited them.

## How much does each kind of filing matter?

`eval/event_study.py` measures it. It takes every filing on NSE over 18 months, **280,528 filings, measured at the 2,284 companies with price history across 492 trading days**, and asks whether the stock moved sharply on the first session that could react. A sharp move is more than 2 of the stock's own recent standard deviations, after removing the market's move. Each filing type is compared with the same stocks on days they filed nothing, when a sharp move happens 5.4% of the time.

The hand-set scores FINN started with only loosely tracked this (rank correlation 0.30 across 87 filing types), so they were replaced. Filings within a session of results are excluded, so they can't borrow the results' reaction.

| Filing type | Sharp move | Against a quiet day | Filings |
|---|---|---|---|
| Offer for sale | 35.8% | 6.7× | 53 |
| Quarterly results | 27.7% | 5.2× | 13,123 |
| Press release about results | 21.3% | 4.0× | 268 |
| Order wins | 11.7% | 2.2× | 2,037 |
| Press release, general | 11.3% | 2.1× | 3,627 |
| Press release on an order | 10.2% | 1.9× | 674 |
| Press release on a deal | 8.9% | 1.7× | 906 |
| A top executive leaving | 8.8% | 1.6× | 1,051 |
| Press release on a product launch | 6.3% | 1.2× | 809 |
| Press release on capacity or a plant | 5.7% | 1.1× | 407 |
| Merger or scheme | 10.5% | 2.0× | 600 |
| Insolvency or tribunal filings | 4.7% | 0.9× | 869 |
| Newspaper ad copies | 5.8% | 1.1× | 17,852 |

**The test that counts:** ranks learned from the first year only, judged on the 72,721 filings of the last 26 weeks, which they never saw.

| Tier | Old hand-set rules | Measured ranks |
|---|---|---|
| Top | 4.66× a quiet day | 4.98× |
| Notable | 1.34× | 1.55× |
| Routine | 1.15× | 1.11× |

Routine got quieter and notable got sharper. Leaving out results, the top tier barely changes (1.68× against 1.65×), because top-executive changes stay there on purpose. Splitting press releases by content barely moved these totals (they were 5.00×, 1.56× and 1.10× before it). What it changed is which press releases an investor sees.

One first attempt went wrong: the word "financial" in "financial services" labelled a Persistent partnership as results and sent it to the top tier. A check on real filings caught it, and the rule was tightened before the numbers above were measured.

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
| Routine paperwork correctly buried | 28 of 28 | 28 of 28 |
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
| Filing headlines | 729 | 0 |
| Promise summaries | 503 | 0 |
| Filing details | 191 | 0 |
| Bull and bear points | 137 | 0 |
| A person's background | 131 | 0 |
| Company KPIs | 122 | 0 |
| Annual report notes | 40 | 1 |
| Verdict notes | 37 | 0 |
| Business descriptions | 22 | 0 |

This run, on 2026-11-03, includes the filings read by a free model. The one failure was JP Power's auditor note: its figure is real, but it's on page 35, not the cited page 33. The page was corrected directly in the stored dossier, and the table wasn't rerun afterwards. Code checks an auditor note's figure against the text the model read, not against its cited page, so a model can still get that page number wrong.

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
- Models aren't fully repeatable: Dixon read 2 of 14, 3 of 14 and 3 of 15 across rebuilds. Settled verdicts now stay put on a rebuild unless a new call changes them, which is unit-tested and hasn't yet been through a real new quarter.
- Most statements stay unresolved: 193 "can't verify", 150 "too vague" and 120 "not due yet", against 40 settled. TCS, Titan, Reliance and HDFC Bank have no settled verdicts.

## How good is the free tier?

Everything shipped above was built with Claude. New companies and filings now use free models on Groq, so the same companies were rebuilt that way and compared, on a copy of the database.

| Free-tier build | Time | Tokens | Promises found | Settled verdicts |
|---|---|---|---|---|
| Voltas, new to FINN | 5.5 min | about 38,000 | 7 | 0 |
| Kaynes | 8 min | about 50,000 | 20, against Claude's 42 | 4, against Claude's 4 |
| Dixon, the heaviest | 14 min | about 110,000 | 86, against Claude's 78 | 7, against Claude's 15 |

The two research models have about 380,000 tokens a day between them, so that's **roughly 3 to 10 new companies a day** if nothing else needs them.

**Verdicts, read by Claude against their evidence:**

| Round | Model-made verdicts read | Result |
|---|---|---|
| First build, Kaynes and Voltas | 3 | At least 1 wrong: metering's *share* of revenue used as its *growth*. 1 more looks doubtful |
| Harder thinking and a stricter second reading, Dixon | 8 | 3 clearly wrong, 1 arguable. All 3 had passed the second reading |
| After code checks for those errors, Dixon and Kaynes | 7 | 0 clearly wrong, 1 doubtful (may mix up two Dixon projects), 1 right but on indirect evidence |

Code-calculated verdicts weren't the problem: Kaynes's matched Claude's. The new checks caught none of the 12 Claude-made "delivered" verdicts or any shipped due date, so they don't misfire on good data.

**Limits:**

- **The last row isn't a clean test,** because the checks were written from these same companies. `eval/free_tier_audit.py` can run one on unseen companies, and that hasn't been done yet. Expect a higher error rate than Claude's.
- FINN finds and settles less on the free tier. For Dixon it misses the ₹7,000 crore export target that Claude's build caught. It also overturned verdicts that Claude's record shows were right, such as Chennai's delay, so "can't verify" is more common.
- A company read this way says so on its page. The 23 shipped dossiers remain Claude-built, and a verdict stands from one build to the next unless a new call changes it.

**Filings:** rules read the subject lines of 1,128 older PDFs at no cost. A free model then read 48 of the highest-ranked, at about 2,400 tokens each, and 1,107 remain for later days. Of its 13 demotions from the top tier, some were wrong, including HDFC Bank's chairman resigning and Polycab's new CFO, so code now decides who counts as a top executive and keeps results filings as results. After that, 10 filings changed tier against the earlier ranking, and all 10 were read and look defensible. 56 PDFs couldn't be read, and those filings keep their rule-based rank.

**Chat:** real questions through the app took about a second or less on Qwen and used about 600 tokens each. Chat runs separately, so research can't use up its allowance.

**Small talk:** about 30 real messages went through Qwen and the replies were read by Claude, before anything was tuned and then again after. Questions meant for a company's calls stopped at a stand-in, so this tests where messages go, not Ask's answers. The first run was useful: the model offered a vague opinion on the banking sector, repeated its own instructions back, and sent "and the margins?" to the portfolio view when Dixon was the subject, so market questions no longer reach it and only clear small talk can leave a company's conversation. After the fixes, all 30 went where they should, and none stated a company's facts. That second run is on the messages the fixes came from, so it isn't a clean test. A greeting costs about 320 tokens and takes under 2 seconds, other small talk about 650 because the question is read first, and repeating a greeting costs nothing. An unusual message can still get a weak or wrong general answer, because this model has no sources for it.

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
| Everything spent building this, including rework, experiments and testing | about $10.60 across 1,898 model calls, through the Claude CLI |
| Running it from now on | $0, on Groq's free tier |

Figures come from the app's own ledger, as reported by the provider. Repeat runs on unchanged documents use no tokens because answers are cached. The free tier has its own limits, described above, and chat has a daily limit too, since a public link can be asked anything.

## Software tests

242 automated tests (`pytest tests`, with no network or model) and 30 browser checks at phone size (`tests/e2e.py`), run in Chromium, WebKit (Safari's engine) and Firefox. An accessibility scan found no serious issues in either theme, and phone-size checks ran from 320 to 768 pixels wide. Some inline source links are under 24 pixels tall, and a dossier takes about 5 seconds to open on simulated slow 4G. Firefox has failed now and then: one run failed a click on the Brief, which took 3 dependent checks with it, and a later run failed 1 check that wasn't recorded. The runs after each passed in full. The cause wasn't found, so treat it as an intermittent problem. WebKit also logs a cancelled calendar request during these runs, without any check failing, and that wasn't traced either.

**Tested only in parts:** the "Moving now" banner. The live endpoint ran against real quotes during market hours, and the banner was checked on screen with the clock held at market hours and the live answer filled in, but the two haven't been seen together.

**Not tested:** phone notifications on a real phone (they're off by default, and the sending logic and its safeguards have unit tests only), real calls to the Anthropic API (a local stub and the Claude CLI only), Gemini, the actual Docker build, whether NSE accepts requests from a cloud host, and use by real investors.

**On a free host:** Render's free disk is wiped on each restart, so the app wakes with the shipped snapshot. Any company added, old filing read or day's usage counted since then is forgotten. Groq's own limits still apply, and FINN obeys its wait times when it's told to. Read older filings locally and commit the database to keep that work.
