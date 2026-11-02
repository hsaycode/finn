# Decisions and what changed them

A log of the calls that shaped FINN, including the ones real data overturned.

## Product

**The product is a record of whether management keeps its word.** Summarising a call is a commodity. Checking last year's promise against this quarter's filing is what an analyst does, and the two sides of the app share it: a new result gets judged against the guidance for that quarter.

**A brief that ends.** An investor doesn't need another feed. The brief counts down, says "That's everything", and shows how many filings it buried, so the silence can be trusted.

**Alert budget instead of a bare threshold.** Moving a limit shows how many alerts it would have fired over the last 30 sessions. People can't judge "2× volume" in the abstract, but they can judge "7 interruptions a week".

**Chat is not the interface.** An investor often doesn't know what to ask, and open-ended chat is where invented answers live. Ask exists, but only on one company's transcripts, and it can reply that the calls don't cover something.

**No buy or sell calls.** FINN says what changed and leaves the decision to the investor.

## What real data changed

| I assumed | What the data showed | What changed |
|---|---|---|
| Filing titles describe filings | Orders, ratings and acquisitions often repeat their category name; the content is in the PDF | Selective PDF reading, with the letter's "Sub:" line as a free real title |
| NSE's labels can be trusted | "Credit Rating" contains ESG scores and voluntary withdrawals; "General Updates" contains completed acquisitions | Labels only decide whether a PDF is worth opening |
| Results need PDF parsing and a model | NSE publishes the exact XBRL the company filed | Results are parsed by code, at no cost |
| Reported profit is the headline | Vodafone Idea shows ₹51,970 cr profit between losses of over ₹5,000 cr; Dixon's −49% is partly a high-other-income base | Cards flag one-offs and distorted comparisons |
| All amounts scale with company size | A ₹2.79 cr petition admitted Polycab into insolvency | Admission is scored as serious at any amount |
| One filing, one card | Polycab's dispute produced 9 filings over 3 weeks and ended with the order set aside | Stories are threaded, ranked by their worst moment and told from their latest |

## Trust

- **Models choose sentence numbers, not words.** Every quote on screen is verbatim source text, so a quote can't be invented. Joining sentences was still a way to mislead: a check against the PDFs found 18% of quotes read as one passage that the document didn't contain. Quotes now keep to one speaker and one call, and gaps are marked.
- **Models see the sentence before the one they cite.** "Should hit ₹7,000 crores" means nothing alone: the line before it says exports. Reading sentences in isolation caused most of the wrong verdicts an audit found, so this costs more tokens and is worth them.
- **Code won't score what it can't identify.** An unnamed margin isn't compared with filed EBITDA, a due date can't contradict "over the next few quarters", and a verdict is dropped if its explanation cites a figure the reader can't see.
- **A figure that isn't in its cited sentence gets dropped.** Headline figures must also appear in the filing.
- **Code does all arithmetic.** Dixon's model-written headline said profit was "up 45%" when filings show it fell 49%. Results headlines now come from code only.
- **Segment targets aren't scored against company totals.** An early version marked a ₹500 cr subsidiary target "delivered" against ₹1,580 cr of total revenue. The model now labels scope, with a sanity check behind it.
- **The company's name is hidden from the analysis models**, to enforce the primary-sources rule rather than just ask for it.
- **Each model-made verdict gets a second opinion.** A separate pass tries to overturn it, reading the text around both the promise and its evidence. It overturned 9 of 45 verdicts in the final build. See `docs/RESULTS.md`.
- **"Can't verify" is a real answer**, and the app shows those promises rather than guessing.
- **Failures are reported as failures.** "Check the ticker" only follows a real 404, "not mentioned" only follows reading the calls, and "filings checked" only follows reaching a source. Each of these used to say something false when a source was down.
- **Filing text is untrusted input.** It's sent fenced, as data, to models with no tools.

## Models and cost

| Step | Model | Why |
|---|---|---|
| Sorting the obvious paperwork, about half of all filings | None (rules) | Free, and good enough for paperwork |
| Results figures | None (XBRL) | Exact, and a model could only add errors |
| Reading filing PDFs | Claude Haiku 5.5 | Structured extraction at about $0.001 each |
| Extracting promises | Claude Haiku 5.5 | Input pre-filtered to about 10% of each transcript |
| Judging promises, reviewing verdicts, bull and bear | Claude Sonnet 5.5 | Chosen by comparison, see below |

**Sonnet over Opus for judgment.** `eval/bakeoff_judge.py` ran both over the same evidence for three companies. They agreed on 69 of 79 verdicts (87%), and neither was clearly better on the other 10, at about 40% of the cost. There's no ground truth for those 10, so this is a judgment call, and swapping is one environment variable.

**Caching.** Answers are stored by content hash, so re-running on an unchanged filing costs nothing, and the deployed app can replay them with no key at all.

## Architecture

**One process, one SQLite file shipped in the image.** I planned on hosted Postgres and dropped it. A snapshot in the image means a cold start is never an empty app, and if NSE blocks a cloud IP the app still shows its last good data with its date.

**Fallbacks.** No model: filings still rank by rules, and research still shows every computed section and says what's missing. No transcripts: the section says so. Banks: EBITDA and cash-conversion verdicts are replaced with NII, pre-provision profit and NPAs.

## Left out on purpose

Real-time prices (the brief's data is end-of-day), portfolio P&L, logins, news and broker content, and a learning feed. Pledging coverage is partial, since NSE's pledge endpoint returned nothing and it relies on filings instead.
