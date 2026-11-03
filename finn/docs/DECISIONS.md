# Decisions and what changed them

A log of the calls that shaped FINN, including the ones real data overturned.

## Product

**The product is a record of whether management keeps its word.** Summarising a call is a commodity. Checking last year's promise against this quarter's filing is what an analyst does, and the two sides of the app share it: a new result gets judged against the guidance for that quarter.

**A brief that ends.** An investor doesn't need another feed. The brief counts down, says "That's everything", and shows how many filings it buried, so the silence can be trusted.

**Except on someone's first look.** On 41 of 63 recent days nothing in this portfolio ranked top, so a new visitor would usually meet an empty page before knowing what FINN does. Only on that first visit, an empty 24 hours opens on the last 7 days and says so. After that an empty day says "Nothing needs you". Price moves also sit below the filings on a longer window, because six of them were pushing the filings off the screen.

**Alert budget instead of a bare threshold.** Moving a limit shows how many alerts it would have fired over the last 30 sessions. People can't judge "2× volume" in the abstract, but they can judge "7 interruptions a week".

**Chat is a section, not the interface.** An investor often doesn't know what to ask, and open-ended chat is where invented answers live, so the Brief stays the front door. The Chat tab holds alerts and answers questions, but code writes the answers from FINN's records. A model can only work out where to look, or summarise transcript sentences that are shown beside it. It refuses buy or sell questions and says when it can't tell.

**It can still say hello.** "Hi" used to get "I can't tell which company you mean", and mid-conversation it went looking for "hi" in Dixon's earnings calls. Now small talk goes to the chat model, which holds no records and is told not to state anything about a company, sector or market. Code doesn't rely on that: a reply with a figure, an index, advice or a company's name isn't shown. A question about markets never reaches it. Greetings need no model to work, and one saved answer serves everyone who says the same thing, so a "hi" rarely spends any allowance.

**Telegram came out.** It would have reached a single configured chat, which an evaluator or a second investor couldn't join. Alerts moved into the app, and the Brief opens with what's new since your last visit. Browser notifications are built but off until keys are set, so by default nothing reaches you while the app is closed.

**Porter's and PESTLE in management's own words.** An independent view of a sector would need outside knowledge, which the primary-sources rule forbids. So FINN files management's sentences under each heading, and a heading it never discussed says so. A quote stays only if its words belong there, which removed 102 of 262 the model had misfiled.

**The numbers depend on the company.** EBITDA means nothing for a lender or an insurer. Banks and NBFCs show NII and pre-provision profit, and insurers show premiums, underwriting and combined ratios. Each company also shows the operating figures its management chooses to report, with the sentence behind them.

**No buy or sell calls.** FINN says what changed and leaves the decision to the investor.

**Holdings live on the device.** There are no accounts, so each person's list stays in their browser and travels with each request. A company someone adds is followed by the server from then on. A free host forgets that when it restarts, so the app reminds it.

**A calendar of filed dates only.** "Coming up" shows board meetings and ex-dates companies have announced to NSE, and for a results date, what management promised for that quarter. FINN doesn't estimate dates.

**A verdict, once settled, stands.** Models aren't repeatable, and Dixon read 2 of 14, 3 of 14 and 3 of 15 across rebuilds, which is no way to keep score. A rebuild now keeps the earlier verdict unless its new evidence comes from a call that didn't exist before. Verdicts calculated from filings are always recalculated.

## What real data changed

| I assumed | What the data showed | What changed |
|---|---|---|
| Filing titles describe filings | Orders, ratings and acquisitions often repeat their category name; the content is in the PDF | Selective PDF reading, with the letter's "Sub:" line as a free real title |
| NSE's labels can be trusted | "Credit Rating" contains ESG scores and voluntary withdrawals; "General Updates" contains completed acquisitions | Labels only decide whether a PDF is worth opening |
| Results need PDF parsing and a model | NSE publishes the exact XBRL the company filed | Results are parsed by code, at no cost |
| Reported profit is the headline | Vodafone Idea shows ₹51,970 cr profit between losses of over ₹5,000 cr; Dixon's −49% is partly a high-other-income base | Cards flag one-offs and distorted comparisons |
| All amounts scale with company size | A ₹2.79 cr petition admitted Polycab into insolvency | Admission is scored as serious at any amount |
| My own scores reflect importance | Across 2.8 lakh filings they only loosely tracked market reaction (rank correlation 0.30). Offer-for-sale filings were buried yet moved stocks 6.7× as often as a quiet day | Filing types are ranked by measured reaction, with a few documented exceptions |
| A CEO change is the biggest news | Top-executive changes moved stocks 1.6× as often as a quiet day, against 5.2× for results | Results outrank them. They stay in the top tier because the brief names them as material |
| Press releases are one kind of filing | They were the largest group in "Worth knowing". Split by content, those about results moved stocks sharply 4.0× as often as a quiet day, orders 1.9×, and product launches 1.2× and new plants 1.1×, close to a quiet day | Each kind has its own measured rank, launches and plants drop to routine, and the rest get their own "Company news" shelf |
| Every filing type behaves independently | Recordings and decks appeared to move stocks, because they sit next to results and calls | Filings within a session of results are excluded, and those types keep their low rank |
| Transcripts exist for every company | About a quarter of random companies show no sign of holding investor calls | Annual reports stand in for them, and the app says when they do |
| NBFCs and insurers file like everyone else | NBFCs were read as banks and insurers use another taxonomy, so their cards came out blank | Each format now gets its own parser and headline numbers |
| One filing, one card | Polycab's dispute produced 9 filings over 3 weeks and ended with the order set aside | Stories are threaded, ranked by their worst moment and told from their latest |
| The deployed app would keep itself current | A refresh from Render failed for all 18 holdings, and one failure stopped each company's prices too, so the live app froze at its snapshot | Prices no longer depend on NSE. Filings, results and dates fall back to NSE's archive feeds, then screener.in, and `/api/status` shows which source answered |

## Trust

- **Everything cites its source.** A figure, a quote, a price or an alert each links to the document behind it, and a report ends with a list of them all. The one answer with nothing to link, a general explanation of a term in chat, says so.
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
| Reading filing PDFs | Shipped: a low-cost paid model. New: gpt-oss-20b on Groq | Structured extraction |
| Extracting promises | Shipped: a low-cost paid model. New: gpt-oss-20b on Groq | Input pre-filtered to about 10% of each transcript |
| Judging promises, reviewing verdicts, bull and bear | Shipped: a stronger paid model. New: gpt-oss-120b on Groq | Chosen by comparison, see below |
| Reading chat questions, summarising transcript sentences, small talk | Qwen 3.8 27B on Groq | Fast enough for a conversation. It's a preview model, so a figure missing from its quotes isn't shown, and an outage falls back to the passages |
| Live limits, calendar, follow-up questions, one-page summary | None (code) | None of them needs a model, so none of them can run out of allowance |

**Built around the free tier.** The brief says this is comfortably doable on free tiers, so nothing new may need a paid one. Groq's free limits are per model: 8,000 tokens a minute and 1,000 requests a day as its response headers report them, and 30 requests a minute and 200,000 tokens a day in its docs. So:

- *Separate models for separate jobs.* Chat keeps Qwen to itself, so a long research build can't leave someone's question unanswered.
- *FINN paces itself.* It tracks each model's use by the minute and the day, waits its turn in the background, and gives chat only a few seconds before trying the next model. It stops just short of the daily limit, and obeys Groq's own wait time if it's told to.
- *Big jobs are split.* Long requests would simply be refused, so transcripts go in batches and verdicts a few at a time.
- *Numbers first.* A new company's filed figures appear in seconds and its track record fills in behind them, because reading the calls takes 5 to 15 minutes at this pace.
- *Old filings wait for spare capacity.* Rules read every PDF's subject line at no cost. Models then read the most important first, and only while most of the day's allowance is unused.

**A weaker model gets stricter checks.** The first free-tier audit found 3 clearly wrong verdicts out of 8 on Dixon, and the second reading had agreed with all 3. So on the free tier a verdict stands only if a second reading explicitly agrees, evidence that only restates a plan ("trials will start") can't prove delivery, and a due date can't fall before the call that promised it. A free model also said a chairman and a new CFO weren't top executives, so code now decides that from the job title. The cost is fewer verdicts, and the app says when a company was read by a free model. See `docs/RESULTS.md`.

**A mid-priced model over the most expensive one for judgment.** `eval/bakeoff_judge.py` ran both over the same evidence for three companies. They agreed on 69 of 79 verdicts (87%), and neither was clearly better on the other 10, at about 40% of the cost. There's no ground truth for those 10, so this is a judgment call, and swapping is one environment variable.

**Caching.** Answers are stored by content hash, so re-running on an unchanged filing costs nothing, and the deployed app can replay them with no key at all.

**What gets stored.** The local cache of PDFs and prices reached about 6 GB, and none of it ships. Transcripts go into the database as compressed text, about 20 KB each, so Ask works with no source reachable and loses nothing FINN uses. The market study reduces 2.8 lakh filings and two years of prices to a 32 KB table of evidence per filing type.

## Architecture

**One process, one SQLite file shipped in the image.** I planned on hosted Postgres and dropped it. A snapshot in the image means a cold start is never an empty app.

**More than one way to each fact.** NSE's API failed from the cloud host, so each kind of data now has a fallback that fails on its own: prices from Yahoo, filings and exact XBRL results from NSE's archive feeds, older results from screener.in. Its figures say where they came from and never replace a filed one. The feeds only reach back to midnight, so a script publishes a fresh database from a computer NSE answers.

**Fallbacks.** No model: filings still rank by rules, and research still shows every computed section and says what's missing. No transcripts: the section says so. Banks: EBITDA and cash-conversion verdicts are replaced with NII, pre-provision profit and NPAs.

## Left out on purpose

Portfolio P&L, logins, news and broker content, and a learning feed. Phone notifications, when turned on, still use closing prices and the default portfolio, because they run on the server, which doesn't hold anyone's list. Investor presentations are classified as filings but not analysed, because they're management's own framing and FINN tests management against filed numbers. Pledging and insider coverage is thin: NSE's pledge endpoint returned nothing, so both rely on filings, and few appeared for this portfolio.
