# Decisions and what changed them

A log of the calls that shaped FINN, including the ones real data overturned.

## Product

**The product is a record of whether management keeps its word.** Summarising a call is a commodity. Checking last year's promise against this quarter's filing is what an analyst does, and the two sides of the app share it: a new result gets judged against the guidance for that quarter.

**A brief that ends.** An investor doesn't need another feed. The brief counts down, says "That's everything", and shows how many filings it buried, so the silence can be trusted.

**Alert budget instead of a bare threshold.** Moving a limit shows how many alerts it would have fired over the last 30 sessions. People can't judge "2× volume" in the abstract, but they can judge "7 interruptions a week".

**Chat is a section, not the interface.** An investor often doesn't know what to ask, and open-ended chat is where invented answers live, so the Brief stays the front door. The Chat tab holds alerts and answers questions, but code writes the answers from FINN's records. A model can only work out where to look, or summarise transcript sentences that are shown beside it. It refuses buy or sell questions and says when it can't tell.

**Telegram came out.** It would have reached a single configured chat, which an evaluator or a second investor couldn't join. Alerts moved into the app, and the Brief opens with what's new since your last visit. Browser notifications are built but off until keys are set, so by default nothing reaches you while the app is closed.

**Porter's and PESTLE in management's own words.** An independent view of a sector would need outside knowledge, which the primary-sources rule forbids. So FINN files management's sentences under each heading, and a heading it never discussed says so. A quote stays only if its words belong there, which removed 102 of 262 the model had misfiled.

**The numbers depend on the company.** EBITDA means nothing for a lender or an insurer. Banks and NBFCs show NII and pre-provision profit, and insurers show premiums, underwriting and combined ratios. Each company also shows the operating figures its management chooses to report, with the sentence behind them.

**No buy or sell calls.** FINN says what changed and leaves the decision to the investor.

## What real data changed

| I assumed | What the data showed | What changed |
|---|---|---|
| Filing titles describe filings | Orders, ratings and acquisitions often repeat their category name; the content is in the PDF | Selective PDF reading, with the letter's "Sub:" line as a free real title |
| NSE's labels can be trusted | "Credit Rating" contains ESG scores and voluntary withdrawals; "General Updates" contains completed acquisitions | Labels only decide whether a PDF is worth opening |
| Results need PDF parsing and a model | NSE publishes the exact XBRL the company filed | Results are parsed by code, at no cost |
| Reported profit is the headline | Vodafone Idea shows ₹51,970 cr profit between losses of over ₹5,000 cr; Dixon's −49% is partly a high-other-income base | Cards flag one-offs and distorted comparisons |
| All amounts scale with company size | A ₹2.79 cr petition admitted Polycab into insolvency | Admission is scored as serious at any amount |
| My own scores reflect importance | Across 280,486 filings they only loosely tracked market reaction (rank correlation 0.31). Offer-for-sale filings were buried yet moved stocks 6.7× as often as a quiet day | Filing types are ranked by measured reaction, with a few documented exceptions |
| A CEO change is the biggest news | Top-executive changes moved stocks 1.6× as often as a quiet day, against 5.2× for results | Results outrank them. They stay in the top tier because the brief names them as material |
| Every filing type behaves independently | Recordings and decks appeared to move stocks, because they sit next to results and calls | Filings within a session of results are excluded, and those types keep their low rank |
| Transcripts exist for every company | About a quarter of random companies show no sign of holding investor calls | Annual reports stand in for them, and the app says when they do |
| NBFCs and insurers file like everyone else | NBFCs were read as banks and insurers use another taxonomy, so their cards came out blank | Each format now gets its own parser and headline numbers |
| One filing, one card | Polycab's dispute produced 9 filings over 3 weeks and ended with the order set aside | Stories are threaded, ranked by their worst moment and told from their latest |

## Trust

- **Models choose sentence numbers, not words.** Every quote on screen is verbatim source text, so a quote can't be invented. Joining sentences was still a way to mislead: a check against the PDFs found 18% of quotes read as one passage that the document didn't contain. Quotes now keep to one speaker and one call, and gaps are marked.
- **Models see the sentence before the one they cite.** "Should hit ₹7,000 crores" means nothing alone: the line before it says exports. Reading sentences in isolation caused most of the wrong verdicts an audit found, so this costs more tokens and is worth them.
- **Code won't score what it can't identify.** An unnamed margin isn't compared with filed EBITDA, a due date can't contradict "over the next few quarters", and a verdict is dropped if its explanation cites a figure the reader can't see.
- **A figure that isn't in its cited sentence gets dropped.** Headline figures must also appear in the filing.
- **Code does all arithmetic.** Dixon's model-written headline said profit was "up 45%" when filings show it fell 49%. Results headlines now come from code only.
- **Segment targets aren't scored against company totals.** An early version marked a ₹500 cr subsidiary target "delivered" against ₹1,580 cr of total revenue. The model now labels scope, with a sanity check behind it.
- **The company's name is hidden from the analysis models**, to enforce the primary-sources rule rather than just ask for it.
- **Each model-made verdict gets a second opinion.** A separate pass tries to overturn it, reading the text around both the promise and its evidence. It overturned 5 of 42 verdicts in the final build. See `docs/RESULTS.md`.
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
| Reading chat questions, summarising transcript sentences | Qwen 3.8 27B on Groq | Fast enough for a conversation. It's a preview model, so a figure missing from its quotes isn't shown, and an outage falls back to the passages |

**Sonnet over Opus for judgment.** `eval/bakeoff_judge.py` ran both over the same evidence for three companies. They agreed on 69 of 79 verdicts (87%), and neither was clearly better on the other 10, at about 40% of the cost. There's no ground truth for those 10, so this is a judgment call, and swapping is one environment variable.

**Caching.** Answers are stored by content hash, so re-running on an unchanged filing costs nothing, and the deployed app can replay them with no key at all.

**What gets stored.** The local cache of PDFs and prices reached about 6 GB, and none of it ships. Transcripts go into the database as compressed text, about 20 KB each, so Ask works with no source reachable and loses nothing FINN uses. The market study reduces 2.8 lakh filings and two years of prices to a 32 KB table of evidence per filing type.

## Architecture

**One process, one SQLite file shipped in the image.** I planned on hosted Postgres and dropped it. A snapshot in the image means a cold start is never an empty app, and if NSE blocks a cloud IP the app still shows its last good data with its date.

**Fallbacks.** No model: filings still rank by rules, and research still shows every computed section and says what's missing. No transcripts: the section says so. Banks: EBITDA and cash-conversion verdicts are replaced with NII, pre-provision profit and NPAs.

## Left out on purpose

Portfolio P&L, logins, news and broker content, and a learning feed. Investor presentations are classified as filings but not analysed, because they're management's own framing and FINN tests management against filed numbers. Pledging and insider coverage is thin: NSE's pledge endpoint returned nothing, so both rely on filings, and few appeared for this portfolio.
