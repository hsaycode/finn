**To:** assessment@enam.com
**Subject:** ENAM - IIT screening - 2027

Hello,

Here is my submission for the AI Engineer Build Challenge.

- **Prototype (open on a phone):** [DEPLOYED LINK]
- **Repository:** [GITHUB LINK]
- **AI build history:** [LINK OR ATTACHMENT]

**What I built**

FINN reads every filing across an 18-company portfolio, shows the few that matter, and keeps score of what management promised.

The fastest way to see it is Research → `KAYNES`. Management guided FY26 revenue of ₹4,500 crore plus, later spoke of ₹4,000 crore, and filed ₹3,626 crore. The quote, the page and the source are one tap away. Dixon kept 3 of 14 checkable promises, with deadlines that moved repeatedly. Then try a ticker of your own.

- **Push:** a brief that ends. It ranks filings, groups repeat filings into one story, and buries the routine ones. Results cards show Revenue, EBITDA, PBT and PAT, QoQ and YoY, and flag distortions (banks show NII and PPOP, since EBITDA doesn't apply). Dixon's "profit down 49%" is shown with the unusually high other income in its year-ago base. Management changes carry the person's background from the company's filings, plus a LinkedIn search.
- **Pull:** a track record for any NSE ticker, with bull and bear cases, guidance, balance sheet and cash quality, using primary sources only.
- **The link between them:** a new result is checked against what was promised for that quarter.

**Key decisions**

- **Interaction:** I didn't lead with chat, because an investor often doesn't know what to ask, and open-ended chat is where invented answers live. Questions exist, but only against a company's own transcripts. Alert limits show how many alerts each setting would have sent, so "unusual" becomes a choice about interruptions.
- **Trust by construction:** models choose sentences by number, so quotes are verbatim. Code does the arithmetic. Results figures come from the filed XBRL. The company's name is hidden from the analysis models, to enforce the primary-sources rule. A second pass tries to overturn each model-made verdict, and "can't verify" is a real answer.
- **Testing changed the design:** checking every quote against its PDF showed 18% had joined separate sentences as one passage, so gaps are now marked. Auditing verdicts against the surrounding text showed about 1 in 4 were wrong or unsupported, mostly because models read sentences out of context: an export target was judged on a telecom figure. I fixed the cause rather than the cases.
- **Cost:** rules first, Haiku for reading, Sonnet for judgment, and caching throughout. I compared Sonnet with Opus on the same evidence and chose Sonnet at about 40% of the cost. A filing costs about $0.001 to read, and a new company costs about $0.04 to $0.20. The whole build, including rework and testing, cost about $8 in model calls.

**How well it works, and its limits** (details in `docs/RESULTS.md`)

- **Ranking:** on a held-out set of 42 filings, all 28 routine ones were buried and all 5 top-ranked ones deserved attention. It missed 1 of 4 important filings: an insolvency admission judged too small to matter. I've fixed that rule, but the sample is small, and the labels came from the AI I built this with, not from an investor.
- **Figures:** 94.4% of 486 results figures match screener.in, and I traced all 27 differences. In 24, FINN shows the filed figure and screener defines the line differently. One, at L&T, is a line the cards don't show. Two, at Adani in one quarter, I couldn't fully explain, though its attributable PAT matches the filing.
- **Verdicts:** after the fixes, a re-audit of 39 found 1 wrong, 1 with a wrong due date and 2 arguable, but I'd tuned on those companies. On six unseen companies, all 7 verdicts held up, which is too few to prove much. An AI did these audits, not a person.
- **Other tickers:** 120 of 120 random companies built without a crash. Those runs tested data handling, not promise quality. 85 had a readable transcript.
- **Thin records:** most management statements end up "can't verify" or "too vague", so the track record says little about companies that give little checkable guidance. Some repeated promises still aren't merged.

**Left out on purpose**

Buy or sell calls, portfolio P&L, intraday prices, logins, and news or broker content. Pledging coverage is partial, because NSE's pledge data came back empty. [Telegram alerts for top filings and threshold crossings are built, but were tested only against a fake client: UPDATE IF YOU TEST THEM. Either way, say that these go to one configured chat, and that in the app itself alerts appear when it's opened.]

**Where I'd take it next**

1. Sit with investors and tune the ranking against their judgment, since AI-assigned labels are the weak point of the current evaluation.
2. Let an investor pin a thesis, not just a promise, so filings rank by what would change their mind.
3. Track promises across more than five calls, and use annual reports to test them.

Thank you for your time. I'm happy to walk through any of it.

[YOUR NAME]
[PHONE / EMAIL]
