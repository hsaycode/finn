"""The rules that must hold whatever the data looks like. No network, no model."""
import pytest

from finn import classify as C
from finn import docs
from finn import enrich as E
from finn import nse, promises as P, research
from finn import transcripts as T


def q(end, **kw):
    return nse.Quarter(symbol="X", period_end=end, basis="consolidated", format=kw.pop("format", "indas"), **kw)


# --- classification, from real filings that fooled earlier versions

@pytest.mark.parametrize("label,text,category,tier", [
    ("Copy of Newspaper Publication", "Copy of Newspaper Publication", "unclassified", C.ROUTINE),
    ("Trading Window", "The Trading Window closure", "unclassified", C.ROUTINE),
    ("Credit Rating", "ESG score of 83.1 assigned", "credit_rating", C.ROUTINE),
    ("Credit Rating", "downgraded to AA-", "credit_rating", C.TOP),
    ("Outcome of Board Meeting", "the financial results for the period ended September 30, 2026", "results", C.TOP),
    ("Analysts/Institutional Investor Meet/Con. Call Updates", "Audio recording of the discussion on the Unaudited Financial Results", "earnings_call", C.ROUTINE),
    ("Updates", "Presentation on the Unaudited Financial Results", "earnings_call", C.ROUTINE),
    ("News Verification", "shares fall after report on internal probe", "rumour", C.NOTABLE),
])
def test_classify(label, text, category, tier):
    v = C.classify(label, text)
    assert v.category == category
    assert C.tier(v.score) == tier


def test_reappointment_is_not_a_change():
    v = C.classify("Appointment", "Re-appointment of Mr X as Independent Director")
    assert C.tier(v.score) == C.ROUTINE


def test_buyback_is_not_a_fund_raise():
    assert C.classify("General Updates", "Board approves buyback of equity shares").category == "buyback"


def test_size_is_relative_to_the_company():
    big, small = C.size_score(2, 200000), C.size_score(2, 50)
    assert big[0] < C.NOTABLE_AT and small[0] >= C.TOP_AT


def test_loan_book_doesnt_count_as_a_transaction():
    card = {"kind": "business_update", "amount_crore": 3307500.0}
    v = C.Verdict("unclassified", 30, "x")
    _, score, _ = E.rescore(v, card, 3307500.0, 500000)
    assert score < C.TOP_AT


def test_no_deviation_is_routine_but_deviation_is_a_red_flag():
    v = C.Verdict("fund_raise", 25, "x")
    assert C.tier(E.rescore(v, {"kind": "fund_raise", "deviation": False}, None, 100)[1]) == C.ROUTINE
    assert C.tier(E.rescore(v, {"kind": "fund_raise", "deviation": True}, None, 100)[1]) == C.TOP


def test_rule_amounts_never_move_rank_without_a_model():
    v = C.Verdict("orders", 50, "x")
    assert E.rescore(v, None, 900000.0, 1000, trust_amount=False)[1] == 50


def test_results_notice_is_not_results():
    v = C.Verdict("unclassified", 30, "x")
    assert E.rescore(v, {"kind": "results", "headline": "Board to meet 29 October to consider results"}, None, 100)[1] < C.NOTABLE_AT


def test_insolvency_admission_matters_at_any_size():
    # From the holdout set: a Rs 2.79 crore petition against a Rs 20,000 crore company.
    v = C.Verdict("litigation", 60, "x")
    card = {"kind": "litigation", "headline": "Tribunal admits creditor's insolvency petition over Rs 2.79 crore dues"}
    assert C.tier(E.rescore(v, card, 2.79, 20000)[1]) == C.TOP


def test_a_small_ordinary_tax_order_stays_buried():
    v = C.Verdict("litigation", 50, "x")
    card = {"kind": "litigation", "headline": "Customs penalty of Rs 18 lakh received"}
    assert C.tier(E.rescore(v, card, 0.18, 900000)[1]) == C.ROUTINE


def test_headline_figures_must_be_in_the_filing():
    assert E.numbers_in_source("Wins Rs 500 crore order", "order worth Rs. 500 crore")
    assert not E.numbers_in_source("Wins Rs 900 crore order", "order worth Rs. 500 crore")


def test_clean_title_rejects_a_bare_label():
    assert E.clean_title("Credit Rating", "Credit Rating") == ""
    assert E.clean_title("A press release dated 0 ,", "Press Release") == ""


# --- fiscal calendar

def test_indian_fiscal_year():
    assert T.fy_quarter_end(1, 27) == "2026-06-30"
    assert T.fy_quarter_end(4, 27) == "2027-03-31"
    assert T.quarter_label("2026-09-30") == "Q2 FY27"
    assert T.quarter_label("2027-03-31") == "Q4 FY27"


def test_call_quarter_must_fit_the_call_date():
    # A May 2026 call talks about FY25 comparisons too, but it's the Q4 FY26 call.
    text = "Q4 FY25 was weak. In Q4 FY25 we said this. Welcome to the Q4 and Full Year FY '26 earnings call."
    assert T._reported_quarter(text, "May 2026") == "2026-03-31"


def test_period_parsing():
    assert P.parse_period("Q3 FY27") == {"end": "2026-12-31", "scope": "quarter"}
    assert P.parse_period("FY27 exit") == {"end": "2027-03-31", "scope": "exit"}
    assert P.parse_period("H2 FY27") == {"end": "2027-03-31", "scope": "half"}
    assert P.parse_period(None) is None


# --- promise scoring

QUARTERS = {
    "2026-03-31": q("2026-03-31", revenue=1000, ebitda=150),
    "2025-12-31": q("2025-12-31", revenue=900, ebitda=130),
    "2025-09-30": q("2025-09-30", revenue=900, ebitda=130),
    "2025-06-30": q("2025-06-30", revenue=800, ebitda=120),
    "2025-03-31": q("2025-03-31", revenue=800, ebitda=100),
    "2024-12-31": q("2024-12-31", revenue=700, ebitda=100),
    "2024-09-30": q("2024-09-30", revenue=700, ebitda=100),
    "2024-06-30": q("2024-06-30", revenue=600, ebitda=90),
}


def promise(**kw):
    return {"metric": "revenue", "low": 4000.0, "period": "FY26", **kw}


def test_full_year_actual_sums_four_quarters():
    assert P.actual(promise(), QUARTERS)["value"] == 3600


def test_missed_revenue():
    r = P.score_financial(promise(low=4500.0), P.actual(promise(), QUARTERS))
    assert r["verdict"] == P.MISSED and "₹3,600 cr" in r["actual"]


def test_exit_margin_tests_only_the_last_quarter():
    a = P.actual(promise(metric="ebitda_margin", period="FY26 exit", low=15.0), QUARTERS)
    assert a["value"] == 15.0


def test_yoy_growth():
    assert P.actual(promise(metric="revenue_growth_yoy", low=10.0), QUARTERS)["value"] == pytest.approx(28.6)


def test_rounding_tolerance_on_a_margin():
    r = P.score_financial(promise(metric="ebitda_margin", low=15.2), {"value": 15.0, "unit": "%", "label": "FY26"})
    assert r["verdict"] == P.DELIVERED


def test_near_miss_is_partly_not_missed():
    r = P.score_financial(promise(metric="ebitda_margin", low=16.0), {"value": 15.0, "unit": "%", "label": "FY26"})
    assert r["verdict"] == P.PARTLY


def test_banks_have_no_ebitda_verdict():
    banks = {k: q(k, format="banking", revenue=100) for k in QUARTERS}
    assert P.actual(promise(metric="ebitda_margin", low=20.0), banks) is None


def test_unreported_period_cannot_be_scored():
    assert P.actual(promise(period="FY29"), QUARTERS) is None


def test_figure_check():
    assert P._figure_in(25.0, "reach 25% by Q4")
    assert P._figure_in(4000.0, "cross INR4,000 crores")
    assert not P._figure_in(25.0, "reach 250 stores")
    assert not P._figure_in(2.5, "12.5% margin")


def test_goalposts_only_compare_the_same_unit():
    base = {"metric": "revenue_growth_yoy", "period": "FY27", "quote": "", "page": 1, "url": ""}
    moves = P.goalposts([
        {**base, "low": 20.0, "unit": "%", "said_period": "2026-03-31", "call_label": "Q4 FY26"},
        {**base, "low": 2.0, "unit": "other", "said_period": "2026-06-30", "call_label": "Q1 FY27"},
    ])
    assert moves == []


def test_goalpost_cut_is_detected():
    base = {"metric": "ebitda_margin", "period": "FY27", "unit": "%", "quote": "", "page": 1, "url": ""}
    moves = P.goalposts([
        {**base, "low": 18.0, "said_period": "2026-03-31", "call_label": "Q4 FY26"},
        {**base, "low": 16.0, "said_period": "2026-06-30", "call_label": "Q1 FY27"},
    ])
    assert len(moves) == 1 and moves[0]["direction"] == "cut"


# --- results card

def test_headline_says_what_happened():
    card = {"banking": False, "label": "Q2 FY27", "flags": [], "metrics": {"revenue": {"yoy": 26.0}, "pat": {"value": 2466, "yoy": -26.4}}}
    assert research.results_headline(card) == "Revenue up 26% on the year, profit down 26%"


def test_headline_reports_a_loss_plainly():
    card = {"banking": False, "label": "Q1", "flags": [], "metrics": {"revenue": {"yoy": 6.0}, "pat": {"value": -3754, "yoy": None}}}
    assert "loss of ₹3,754 cr" in research.results_headline(card)


def test_distorted_base_is_flagged():
    now = q("2026-09-30", other_income=69, pbt=530, ebitda=597)
    base = q("2025-09-30", other_income=496, pbt=922, ebitda=561)
    flags = research._base_flags(now, base)
    assert flags and flags[0]["kind"] == "other_income" and "+6%" in flags[0]["text"]


# --- identity masking and speaker roles

def test_mask_hides_the_company():
    masked = T.mask("Kaynes Technology India Ltd said Kaynes will grow", T.mask_terms("Kaynes Technology India Ltd"))
    assert "Kaynes" not in masked


def test_analysts_are_recognised():
    assert T._role("Achal Lohade", {"achal lohade"}) == "analyst"
    assert T._role("Moderator", set()) == "moderator"
    assert T._role("N. Muthukumar", {"achal lohade"}) == "management"


# --- found by auditing real verdicts

def _p(**kw):
    base = {"metric": "other", "low": None, "high": None, "period": None, "said_period": "2026-03-31", "call_label": "Q4 FY26", "verdict": P.NOT_DUE, "note": "", "kind": "operational", "claim": "x"}
    return {**base, **kw}


def test_a_percent_target_far_from_the_company_figure_is_doubted():
    # Titan: 13-14% guided, 44.9% reported. L&T: 8.5% target, 10.2% reported company-wide.
    assert P._scope_doubt({"metric": "revenue_growth_yoy", "low": 13.0, "high": 14.0}, {"unit": "%", "value": 44.9})
    assert P._scope_doubt({"metric": "ebitda_margin", "low": 8.5, "high": 8.5}, {"unit": "%", "value": 10.2})


def test_a_close_result_is_scored_not_doubted():
    assert not P._scope_doubt({"metric": "ebitda_margin", "low": 18.0, "high": 20.0}, {"unit": "%", "value": 18.8})
    assert not P._scope_doubt({"metric": "ebitda_margin", "low": 15.6, "high": 15.6}, {"unit": "%", "value": 15.8})


def test_a_restated_promise_counts_once_and_shows_its_moving_deadline():
    items = [
        _p(said_period="2025-06-30", call_label="Q1 FY26", period="Q1 FY27", verdict=P.MISSED, note="late", claim="JV live"),
        _p(said_period="2025-09-30", call_label="Q2 FY26", period="Q2 FY27", verdict=P.MISSED, claim="JV live"),
        _p(said_period="2026-06-30", call_label="Q1 FY27", period="Q4 FY27", verdict=P.NOT_DUE, claim="JV live"),
        _p(said_period="2026-06-30", call_label="Q1 FY27", claim="other thing"),
    ]
    out = P.merge_restated(items, [[0, 1, 2]])
    assert len(out) == 2
    assert out[0]["deadline_moves"] == ["Q1 FY27", "Q2 FY27", "Q4 FY27"]
    assert out[0]["verdict"] == P.MISSED and out[0]["restated"] == ["Q2 FY26", "Q1 FY27"]


def test_a_merged_card_shows_the_promise_its_verdict_is_about():
    # Reliance showed "First PV cell line" with a verdict about ingots and wafers.
    items = [
        _p(said_period="2025-06-30", verdict=P.NOT_DUE, claim="First PV cell line", quote="cells"),
        _p(said_period="2025-09-30", call_label="Q2", verdict=P.MISSED, claim="Ingot and wafer lines", quote="wafers", note="still progressing"),
    ]
    out = P.merge_restated(items, [[0, 1]])
    assert len(out) == 1 and out[0]["claim"] == "Ingot and wafer lines" and out[0]["quote"] == "wafers"


def test_a_merged_promise_takes_the_first_settled_verdict():
    items = [_p(said_period="2025-06-30", verdict=P.NO_EVIDENCE), _p(said_period="2025-09-30", call_label="Q2", verdict=P.DELIVERED, note="done")]
    out = P.merge_restated(items, [[0, 1]])
    assert out[0]["verdict"] == P.DELIVERED and out[0]["note"] == "done"


def test_bad_group_indexes_are_ignored():
    items = [_p(), _p()]
    assert len(P.merge_restated(items, [[0, 99], ["a", 1]])) == 2


def test_an_analyst_day_isnt_an_earnings_call():
    talk = [T.Sentence(f"c0.s{i}", 1, name, "management", "We will grow.") for i, name in enumerate(["CEO Name", "CFO Name", "COO Name"] * 5)]
    speech = T.Call(0, "Q2", "2025-09-30", "Nov 2025", "u", 1, talk)
    call = T.Call(0, "Q2", "2025-09-30", "Oct 2025", "u", 1, [T.Sentence("c0.s0", 1, "Moderator", "moderator", "Welcome.")])
    assert not speech.is_earnings_call and call.is_earnings_call


def test_a_january_call_is_about_the_december_quarter():
    # TCS writes 'third quarter of FY 2026', and mentions the second quarter too.
    text = "Results for the third quarter of FY 2026. Compared with Q2 FY26."
    assert T._reported_quarter(text, "Jan 2026") == "2025-12-31"


def test_an_html_page_is_not_accepted_as_a_pdf(tmp_path, monkeypatch):
    # A real case: a transcript link answering 200 with an HTML interstitial.
    from finn import docs
    page = tmp_path / "page"
    page.write_bytes(b"<!doctype html><html>Open on Manuals+</html>")
    monkeypatch.setattr(docs, "fetch_file", lambda *a, **k: page)
    with pytest.raises(docs.NotAPdf):
        docs.load("https://example.com/x.pdf")


def test_requests_carry_the_right_referer():
    assert "nseindia" in T.headers_for("https://nsearchives.nseindia.com/a.pdf")["Referer"]
    assert "bseindia" in T.headers_for("https://www.bseindia.com/a.pdf")["Referer"]


def _doc(pages):
    from finn import docs
    return docs.Doc(url="u", pages=pages)


def test_letter_headers_are_not_speakers():
    body = "Sub: Transcript of the call held today for the quarter.\nScrip Code: 500325\n" \
        "Moderator: Welcome everyone to the call today.\nThe next question is from the line of Asha Rao from Fund.\n" \
        "Asha Rao: What is the margin target for next year?\nRavi Kumar: We expect margins of 20 percent next year.\n"
    call = T.parse(_doc([body]), 0, "Oct 2026", "u")
    speakers = {s.speaker for s in call.sentences}
    assert "Sub" not in speakers and "Scrip Code" not in speakers
    roles = {s.speaker: s.role for s in call.sentences}
    assert roles["Asha Rao"] == "analyst" and roles["Ravi Kumar"] == "management"


def test_a_format_with_no_speaker_labels_is_kept_as_an_earnings_call():
    # Reliance's transcripts label nobody, and an early filter threw them all out.
    body = "Sub: Transcript of the analyst meet held today for the quarter ended.\n" + "We will commission the new plant by next year as planned. " * 60
    call = T.parse(_doc([body]), 0, "Oct 2026", "u")
    assert call.is_earnings_call and call.sentences
    assert all(s.speaker == "" and s.role == "unknown" for s in call.sentences)


def test_a_question_isnt_a_candidate_when_nobody_is_labelled():
    call = T.Call(0, "Q2", "2026-09-30", "Oct 2026", "u", 1, [
        T.Sentence("c0.s0", 1, "", "unknown", "Will margins reach 20 percent by FY27?"),
        T.Sentence("c0.s1", 1, "", "unknown", "We expect margins of 20 percent by FY27."),
    ])
    assert [s.id for s in T.candidates(call)] == ["c0.s1"]


def test_beating_a_revenue_target_is_delivered():
    assert P._consistent(P.PARTLY, "Smart metering revenue of INR700-800 crores", "FY26 revenue was INR971 crore, above guidance") == P.DELIVERED


def test_above_a_working_capital_target_stays_a_miss():
    assert P._consistent(P.PARTLY, "Working capital days around 70", "Days were 122, above the target") == P.PARTLY


def test_collecting_facts_survives_missing_history():
    # Nestle: no early cash-cycle value crashed the whole dossier.
    sheet = {"banking": False, "de": [None, 0.1], "columns": ["Mar 2022", "Mar 2026"], "negative_net_worth": False,
             "days": {"ccc": [None, 12.0], "columns": ["Mar 2022", "Mar 2026"]}}
    facts = research.collect_facts({}, None, sheet, None, None)
    assert any("Cash conversion cycle 12 days" == f["text"] for f in facts)


def test_false_speakers_from_a_letter_dont_make_it_look_like_a_speech():
    # Reliance: "Regd. Office:", "Encl.:" and "Duration:" looked like three people who never take questions.
    letter = [T.Sentence(f"c0.s{i}", 1, name, "management", "x") for i, name in enumerate(["Regd. Office", "Encl.", "Duration"])]
    body = [T.Sentence(f"c0.s{i + 3}", 1, "Meeting Audio", "management", "x") for i in range(50)]
    assert T.Call(0, "Q2", "2026-09-30", "Nov 2026", "u", 1, letter + body).is_earnings_call


def test_timestamped_speakers_are_recognised():
    body = (
        "Call Participants:\n \u2022 Sh V Srikanth, CFO, The Company\n \u2022 Sh Anshuman Thakur, Head of Strategy, The Company\n"
        "Sh V Srikanth 00:00:01 \u2013 00:09:00 (Group)\nWe will commission the plant next year as we said.\n"
        "Sh Anshuman Thakur 00:09:00 \u2013 00:12:00 (Digital)\nWe expect subscribers to keep growing strongly.\n"
        "Ravi Analyst 01:10:00 \u2013 01:11:00\nWhen will the new energy plant start producing?\n"
    )
    call = T.parse(_doc([body]), 0, "Nov 2026", "u")
    assert call.timed and call.is_earnings_call
    assert {s.speaker for s in call.sentences} == {"Sh V Srikanth", "Sh Anshuman Thakur", ""}  # no name for someone not on the list
    assert {s.role for s in call.sentences} == {"unknown"}


def test_delivered_cannot_say_it_fell_below_the_target():
    note = "ARPU grew 3.5% year-on-year, though below 4-5% range."
    assert P._consistent(P.DELIVERED, "Expects ARPU to rise 4-5%", note) == P.PARTLY


def test_a_plain_delivered_is_left_alone():
    assert P._consistent(P.DELIVERED, "Plant live by March", "Management says the plant started in February.") == P.DELIVERED


# --- second opinion

def _settled(**kw):
    return _p(verdict=P.DELIVERED, quote="We will do it", note="done", evidence={"quote": "It was done", "call_label": "Q1", "page": 1, "url": "u", "speaker": ""}, **kw)


def test_an_overturned_verdict_becomes_cant_verify(monkeypatch):
    items = [_settled(claim="a"), _settled(claim="b")]
    monkeypatch.setattr(P.llm, "ask_json", lambda *a, **k: [{"key": "r0", "upheld": False, "why": "Evidence is about another plant"}, {"key": "r1", "upheld": True}])
    P.second_opinion(items, [])
    assert items[0]["verdict"] == P.NO_EVIDENCE and "another plant" in items[0]["note"]
    assert items[0]["overturned"]["verdict"] == P.DELIVERED
    assert items[1]["verdict"] == P.DELIVERED


def test_no_reviewer_leaves_verdicts_alone(monkeypatch):
    def none(*a, **k):
        raise P.llm.NoModel("x")
    items = [_settled()]
    monkeypatch.setattr(P.llm, "ask_json", none)
    P.second_opinion(items, [])
    assert items[0]["verdict"] == P.DELIVERED


def test_a_nonsense_review_key_changes_nothing(monkeypatch):
    items = [_settled()]
    monkeypatch.setattr(P.llm, "ask_json", lambda *a, **k: [{"key": "r7", "upheld": False}, {"key": "x", "upheld": False}, "junk"])
    P.second_opinion(items, [])
    assert items[0]["verdict"] == P.DELIVERED


def test_delivered_with_a_hedged_note_isnt_delivered():
    assert P._consistent(P.DELIVERED, "Capacity growth", "Revised upward; no later sentence confirms the actual.") == P.NO_EVIDENCE


def test_a_business_line_target_isnt_a_company_goalpost():
    qs = {k: q(k, revenue=12500) for k in ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")}
    assert not P._company_sized({"metric": "revenue", "from": 7000.0, "to": 5200.0}, qs)
    assert P._company_sized({"metric": "revenue", "from": 50000.0, "to": 45000.0}, qs)
    assert P._company_sized({"metric": "ebitda_margin", "from": 20.0, "to": 18.0}, qs)


def test_a_small_revenue_target_is_labelled_a_business_line():
    qs = {k: q(k, revenue=12500) for k in ("2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")}
    small, big = _p(unit="crore", low=7000.0, claim="FY26 revenue to reach INR7,000 crores"), _p(unit="crore", low=45000.0, claim="FY27 revenue of INR45,000 crores")
    P._label_business_lines([small, big], qs)
    assert small.get("scope") == "segment" and big.get("scope") != "segment"


def test_something_else_called_results_isnt_results():
    v = C.Verdict("unclassified", 30, "x")
    card = {"kind": "results", "headline": "Jio Platforms shares start trading on the exchanges on 28 October"}
    source = "Trading in equity shares of JPL will commence. Further, a board meeting is scheduled to consider financial results."
    assert E.rescore(v, card, None, 100, source_text=source)[0] == "press_release"
    assert C.classify("General Updates", source).category != "results"


def test_real_results_stay_results():
    v = C.Verdict("unclassified", 30, "x")
    card = {"kind": "results", "headline": "Board approved the quarter's figures"}
    assert E.rescore(v, card, None, 100, source_text="Outcome of Board Meeting")[1] == 90


def test_quotes_mark_gaps_and_keep_to_one_speaker_and_call():
    S = T.Sentence
    a, b, c = S("c1.s10", 4, "Ann", "management", "One."), S("c1.s11", 4, "Ann", "management", "Two."), S("c1.s40", 9, "Ann", "management", "Far away.")
    other_speaker, other_call = S("c1.s12", 4, "Bob", "management", "Bob's."), S("c0.s10", 2, "Ann", "management", "Later call.")
    assert T.quote_from([a, b])["quote"] == "One. Two."
    q = T.quote_from([c, a, other_speaker, other_call])  # cited out of order, as a model can
    assert q["quote"] == "One. […] Far away."
    assert q["page"] == 4 and q["pages"] == [4, 9] and q["speaker"] == "Ann"


def _call_with(*texts):
    call = T.Call(0, "Q1 FY26", "2025-06-30", "Aug 2025", "https://x/a.pdf", 5)
    call.sentences = [T.Sentence(f"c0.s{i}", 1, "Ann", "management", t) for i, t in enumerate(texts)]
    return call


def test_an_unnamed_margin_isnt_scored_as_ebitda():
    call = _call_with("What changed the jump from 23% to 26%?", "We maintain our guidance that it will be about 22% to 23%.")
    assert not P._margin_named("ebitda_margin", call, call.sentences[1], call.sentences[1].text)


def test_a_margin_named_nearby_or_in_the_quote_is_scored():
    call = _call_with("EBITDA margin was strong this quarter.", "We should stay at 15% for the year.")
    assert P._margin_named("ebitda_margin", call, call.sentences[1], call.sentences[1].text)
    assert not P._margin_named("ebitda_margin", call, call.sentences[1], "We hold EBITDA flat and a contribution margin of 23%.")


def test_nearby_gives_the_words_around_a_sentence():
    call = _call_with("Exports last year were 1,600 crores.", "This year should hit 7,000 crores.", "We are working on another order.")
    assert call.nearby(call.sentences[1], after=1) == ("Exports last year were 1,600 crores.", "We are working on another order.")
    assert call.nearby(call.sentences[0]) == ("", "")


def _bank_xbrl(pbt, exceptional, segment_pbt):
    return f"""<xbrl xmlns:i="x" xmlns:xbrli="y">
    <xbrli:context id="OneD"><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
    <i:InterestEarned contextRef="OneD">1000000000</i:InterestEarned><i:InterestExpended contextRef="OneD">500000000</i:InterestExpended>
    <i:ExceptionalItems contextRef="OneD">{exceptional}</i:ExceptionalItems><i:ProfitLossFromOrdinaryActivitiesBeforeTax contextRef="OneD">{pbt}</i:ProfitLossFromOrdinaryActivitiesBeforeTax>
    <i:ProfitLossForThePeriod contextRef="OneD">2070000000</i:ProfitLossForThePeriod><i:ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates contextRef="OneD">2070000000</i:ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates>
    <i:SegmentProfitBeforeTax contextRef="OneD">{segment_pbt}</i:SegmentProfitBeforeTax></xbrl>""".encode()


META = {"period_end": "2026-09-30", "basis": "consolidated"}


def test_a_bank_filing_minority_as_exceptional_is_corrected_only_when_segments_confirm_it():
    q = nse.parse_xbrl(_bank_xbrl(2782000000, -83000000, 2865000000), "HDFCBANK", META)
    assert (q.pbt, q.exceptional, q.pat, q.pat_total) == (286.5, 0.0, 207.0, 215.3)
    # A genuine one-off, where segments agree with the filed PBT, stays as filed.
    q = nse.parse_xbrl(_bank_xbrl(2782000000, -83000000, 2782000000), "SOMEBANK", META)
    assert (q.pbt, q.exceptional) == (278.2, -8.3)


def test_next_few_quarters_cant_fall_due_within_one_quarter():
    quote = "We are expanding at giga scale in next few quarters, ramped up during the current year."
    assert not P._period_fits("FY26", quote, "2025-12-31")
    assert P._period_fits("FY27", quote, "2025-12-31")
    assert P._period_fits("FY26", "Ramped up during the current year.", "2025-12-31")
    assert P._period_fits(None, quote, "2025-12-31")


def test_a_note_cant_rest_on_a_figure_nobody_can_see():
    claim, said = "FY26 smartphone volumes of 42 to 43 million units", "Our final numbers of 42 million, 43 million."
    assert not P._note_is_sourced("FY26 volumes closed near 32 million.", "Volumes will be almost similar.", claim, said)
    assert P._note_is_sourced("FY26 volumes closed near 32 million.", "We closed at almost 32-odd million.", claim, said)
    assert P._note_is_sourced("Now Q3 FY27, not April 2026.", "Operations from Q3.", "Plant by April '26", "by April '26")
    assert P._note_is_sourced("Revenue INR1,300 crores, above target.", "about INR1,300-odd crores", "", "")


def test_a_record_built_on_old_calls_says_so():
    call = T.Call(0, "Q2 FY22", "2021-09-30", "Nov 2021", "https://x/a.pdf", 5)
    recent = T.Call(0, "Q4 FY26", "2026-03-31", "May 2026", "https://x/b.pdf", 5)
    q = nse.Quarter(symbol="X", period_end="2026-06-30", basis="consolidated", format="indas", audited=False, filed_at=None, source_url=None)
    assert "Q2 FY22" in research._old_calls_note([call], [q])
    assert research._old_calls_note([recent], [q]) is None
    assert research._old_calls_note([], [q]) is None


def _filing_xbrl(**tags):
    body = "".join(f'<i:{k} contextRef="OneD">{v}</i:{k}>' for k, v in tags.items())
    return f"""<xbrl xmlns:i="x" xmlns:xbrli="y"><xbrli:context id="OneD"><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>{body}</xbrl>""".encode()


def test_an_nbfc_gets_nii_and_preprovision_profit_built_from_filed_lines():
    q = nse.parse_xbrl(_filing_xbrl(InterestEarned=219360000000, FinanceCosts=86020000000, RevenueFromOperations=245460000000, ImpairmentOnFinancialInstruments=21660000000, ProfitBeforeTax=85330000000, ProfitOrLossAttributableToOwnersOfParent=62660000000), "BAJFINANCE", META)
    assert q.format == "nbfc" and q.nii == 13334.0 and q.ppop == 10699.0 and q.provisions == 2166.0 and q.ebitda is None
    assert research.HEADLINES["nbfc"] == ("nii", "ppop", "pbt", "pat")


def test_a_life_insurer_gets_premiums_and_ratios_on_one_scale():
    q = nse.parse_xbrl(_filing_xbrl(SolvencyRatio=1.97, GrossPremiumIncome=271150000000, NetPremiumIncome=268770000000, IncomeFirstYearPremium=57550000000, ProfitLossBeforeTax=5537000000, ProfitLossAfterTaxAndExtraordinaryItems=5492000000, PersistencyRatio13ThMonth=0.8582), "SBILIFE", META)
    assert q.format == "life_insurer" and q.gross_premium == 27115.0 and q.pat == 549.2 and q.solvency == 1.97 and q.persistency_13m == 85.82


def test_a_general_insurer_gets_underwriting_and_a_combined_ratio():
    q = nse.parse_xbrl(_filing_xbrl(SolvencyRatio=0.0275, GrossPremiumsWritten=77020000000, NetPremiumWritten=62130000000, UnderwritingProfitOrLoss=-3184000000, ProfitOrLossBeforeTax=12474000000, ProfitLossAfterTax=9464000000, CombinedRatio=1.0705), "ICICIGI", META)
    assert q.format == "general_insurer" and q.underwriting == -318.4 and q.combined_ratio == 107.05 and q.solvency == 2.75
    assert research.HEADLINES["general_insurer"][2] == "underwriting"


def test_a_real_bank_is_still_a_bank():
    assert nse.parse_xbrl(_bank_xbrl(2782000000, 0, 2782000000), "BANK", META).format == "banking"


def test_annual_report_statements_keep_their_real_page_numbers():
    filler = "Our business grew well in the year and we served many customers across India. " * 3
    page = "Outlook. We expect to commission the new plant at Pune by March 2027. We will expand capacity going forward."
    doc = docs.Doc("https://x/ar.pdf", [filler, page, filler])
    call = T.from_annual_report(doc, 0, 2026, doc.url)
    plant = next(s for s in call.mgmt() if "Pune" in s.text)
    assert plant.page == 2 and call.period_end == "2026-03-31" and call.label == "FY26 annual report"


def test_compressed_text_is_returned_exactly():
    pages = ["Page one, ₹1,000 crore.", "Page two."]
    docs.keep(docs.Doc("https://x/kept.pdf", pages))
    assert docs.load("https://x/kept.pdf").pages == pages and docs.load("https://x/kept.pdf", max_pages=1).pages == pages[:1]


def test_a_link_inside_a_filing_cant_reach_private_addresses(monkeypatch):
    from finn import net

    for url in ("http://example.com/a.pdf", "https://127.0.0.1/a.pdf", "https://10.0.0.5/a.pdf", "https://169.254.169.254/latest/meta-data"):
        with pytest.raises(net.FetchError):
            net.fetch_linked_pdf(url)


def test_a_letter_only_follows_links_that_look_like_transcripts(monkeypatch):
    tried = []
    monkeypatch.setattr(docs, "load_linked", lambda url: tried.append(url) or docs.Doc(url, ["x"]))
    letter = docs.Doc("https://x/l.pdf", ["Please find the transcript at https://co.com/Q2-transcript.pdf and our site https://co.com/about"], [])
    assert research._follow_pointer(letter).url == "https://co.com/Q2-transcript.pdf"
    assert tried == ["https://co.com/Q2-transcript.pdf"]
    assert research._follow_pointer(docs.Doc("https://x/long.pdf", ["p"] * 9, ["https://co.com/transcript.pdf"])) is None


def test_a_bull_point_cant_cite_a_figure_its_sources_dont_hold(monkeypatch):
    facts = [{"text": f"Fact {i}: revenue was 100 crore", "source": "Filed"} for i in range(5)]
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"bull": [{"point": "Revenue was 100 crore", "cites": ["F0"]}, {"point": "Revenue was 999 crore", "cites": ["F1"]}], "bear": [], "swing": "Can it reach 777?"})
    out = research.bull_bear(facts, [])
    assert [x["point"] for x in out["bull"]] == ["Revenue was 100 crore"] and out["swing"] is None


def test_a_claim_with_an_invented_figure_is_dropped(monkeypatch):
    call = _call_with("We will open the plant by Q3 FY27 and it will make 500 units a day in the first year.")
    monkeypatch.setattr(P.llm, "ask_json", lambda *a, **k: [
        {"ids": ["c0.s0"], "claim": "Plant to make 500 units a day", "kind": "operational", "period": "Q3 FY27"},
        {"ids": ["c0.s0"], "claim": "Plant to make 900 units a day", "kind": "operational", "period": "Q3 FY27"},
    ])
    monkeypatch.setattr(P.T, "candidates", lambda c: c.sentences)
    assert [p["claim"] for p in P.extract(call, [])] == ["Plant to make 500 units a day"]


def test_a_persons_line_is_kept_only_if_the_source_supports_it():
    from finn import people

    source = "Mr. Anup Bagchi, aged 57, is a Chartered Accountant. He was Executive Director of ICICI Bank from 2017."
    assert people.grounded("Executive Director, ICICI Bank", source)
    assert not people.grounded("Executive Director, Axis Bank", source)
    assert not people.grounded("Chief Executive, ICICI Bank, 2009", source)
    assert people.grounded("Chartered Accountant", source)


def test_strategic_forces_quote_management_and_leave_gaps_empty(monkeypatch):
    call = _call_with(*[f"Sentence {i} about our competition and market share in the industry today." for i in range(5)])
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"rivalry": [{"id": "c0.s1", "effect": "headwind"}, {"id": "c0.s99", "effect": "headwind"}]})
    out = research.strategic_forces([call], [])
    assert [x["quote"] for x in out["forces"]["rivalry"]] == [call.sentences[1].text]
    assert out["forces"]["legal"] == [] and out["forces"]["rivalry"][0]["effect"] == "headwind"


def test_a_company_kpi_must_be_in_its_sentence(monkeypatch):
    call = _call_with("Our ARPU rose to Rs 218.8 this quarter on strong demand.")
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: [{"name": "ARPU", "value": "Rs 218.8", "id": "c0.s0"}, {"name": "Subscribers", "value": "197 million", "id": "c0.s0"}])
    assert [k["name"] for k in research.company_kpis(call, [])] == ["ARPU"]


def test_a_filing_card_only_keeps_what_the_filing_supports():
    source = "Mr. Rao, aged 57, was Executive Director of ICICI Bank. He resigned for personal reasons. The order is worth Rs 500 crore."
    card = {"amount_note": "worth Rs 900 crore", "response": "Rs 500 crore order", "person": {"name": "Rao", "reason": "Personal reasons",
            "profile": ["Executive Director, ICICI Bank", "Former CEO of HSBC", "57 years old", "61 years old"]}}
    out = E._keep_only_sourced(card, source)
    assert out["amount_note"] is None and out["response"] == "Rs 500 crore order"
    assert out["person"]["profile"] == ["Executive Director, ICICI Bank", "57 years old"] and out["person"]["reason"]


def test_measured_ranks_replace_the_rules_guesses():
    assert C.tier(C.classify("Offer for Sale", "").score) in ("top", "notable") or C.rules("Offer for Sale", "").reason != "Offer for sale"
    assert C.tier(C.classify("Trading Window", "").score) == "routine"
    assert C.tier(C.classify("Outcome of Board Meeting", "Unaudited financial results for the quarter").score) == "top"


def test_filings_show_how_their_kind_has_moved_stocks():
    e = C.evidence_for("New quarterly results filed", "results")
    assert e["big_move_pct"] > e["quiet_pct"] and e["n"] > 1000
    assert C.evidence_for("CFO leaving", "management_change", {"top_executive": True, "action": "leaving"})["n"] > 100
    assert C.evidence_for("Something unknown", "unclassified") is None


def test_delivered_has_to_meet_the_words_that_raise_the_bar():
    assert not P._qualifier_met("Sanand plant fully operational for commercial production", "The Sanand plant is now operational and ramping up.")
    assert P._qualifier_met("Sanand plant fully operational", "The plant is fully operational.")
    assert P._qualifier_met("Plant construction completed by March", "Construction of the plant is complete.")
    assert P._qualifier_met("New store opened in Pune", "We opened the Pune store.")


def test_a_quote_filed_under_a_force_must_be_about_it(monkeypatch):
    call = _call_with("Like the industry, we saw disruption from the West Asia conflict affecting logistics this quarter.", "Our competition has intensified and we are losing market share in the south.", "We expect competition to stay about the same in the coming year overall.")
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"rivalry": [{"id": "c0.s0", "effect": "headwind"}, {"id": "c0.s1", "effect": "headwind"}]})
    assert [x["quote"] for x in research.strategic_forces([call], [])["forces"]["rivalry"]] == [call.sentences[1].text]


def test_compare_copes_with_a_company_missing_a_measure():
    research.save("AAA", {"symbol": "AAA", "name": "A", "generated": "2026-01-01", "balance_sheet": {"days": {"ccc": []}}, "snapshot": {"ratios": {"ROE": "9 %"}}})
    research.save("BBB", {"symbol": "BBB", "name": "B", "generated": "2026-01-01", "balance_sheet": {"days": {"ccc": [None, 12]}}, "snapshot": {"ratios": {"ROE": "5 %"}}})
    rows = {r["name"]: r for r in research.compare(["AAA", "BBB"])["rows"]}
    assert rows["Cash conversion cycle, days"]["values"] == [None, 12]


def test_contingent_total_must_be_printed_on_its_page(monkeypatch):
    page = "Note 41. Contingent liabilities: disputed taxes 120.50, guarantees 300.00, total 420.50 crore. Other figures 10 20 30."
    doc = docs.Doc("https://x/ar.pdf", ["intro", page])
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"printed": "420.50", "unit": "crore", "basis": "consolidated", "page": 2})
    assert research.contingent_liabilities(doc)["contingent_crore"] == 420.5
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"printed": "999.00", "unit": "crore", "page": 2})
    assert research.contingent_liabilities(doc) is None
    monkeypatch.setattr(research.llm, "ask_json", lambda *a, **k: {"printed": "420.50", "unit": "lakh", "page": 2})
    assert research.contingent_liabilities(doc)["contingent_crore"] == 4.21


def test_a_wrong_page_citation_moves_to_where_the_figure_is():
    doc = docs.Doc("https://x/ar.pdf", ["nothing here", "Dues of Rs 47,148 lakhs are recoverable", "more"])
    assert research._page_holding("Dues of Rs 47,148 lakhs", doc, [1, 2, 3], 1) == 2
    assert research._page_holding("Dues of Rs 47,148 lakhs", doc, [1, 2, 3], 2) == 2


def test_a_dotenv_file_fills_gaps_but_never_overrides(tmp_path, monkeypatch):
    import finn

    env = tmp_path / ".env"
    env.write_text('# a comment\nFINN_TEST_A="from file"\nexport FINN_TEST_B=b\nFINN_TEST_C=file\nnot a line\n', encoding="utf-8")
    monkeypatch.delenv("FINN_NO_DOTENV", raising=False)
    monkeypatch.setenv("FINN_TEST_C", "real")
    for name in ("FINN_TEST_A", "FINN_TEST_B"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(finn, "__file__", str(tmp_path / "finn" / "__init__.py"))
    finn._load_env()
    import os
    assert os.environ["FINN_TEST_A"] == "from file" and os.environ["FINN_TEST_B"] == "b" and os.environ["FINN_TEST_C"] == "real"
    for name in ("FINN_TEST_A", "FINN_TEST_B"):
        os.environ.pop(name, None)


def _was_settled(claim, quote, verdict, label="Q1 FY26", evidence_call="Q2 FY26"):
    return {"claim": claim, "quote": quote, "verdict": verdict, "note": verdict, "call_label": label, "said_period": "2025-06-30", "evidence": {"call_label": evidence_call, "quote": "e"}}


def _record(promises, calls=("Q2 FY26", "Q1 FY26")):
    return {"promises": promises, "calls": [{"label": c} for c in calls], "tally": {}, "testable": 0}


def test_a_settled_verdict_survives_a_rebuild():
    old = _record([_was_settled("Plant by March", "The plant opens by March.", "missed")])
    new = _record([_was_settled("Plant by March 2026", "The plant opens by March.", "delivered")])
    out = P.keep_settled(new, {"promises": old})
    assert out["promises"][0]["verdict"] == "missed" and out["tally"]["missed"] == 1 and out["testable"] == 1


def test_new_evidence_from_a_new_call_can_change_a_verdict():
    old = _record([_was_settled("Plant by March", "The plant opens by March.", "missed")])
    new = _record([_was_settled("Plant by March", "The plant opens by March.", "delivered", evidence_call="Q3 FY26")], ("Q3 FY26", "Q2 FY26", "Q1 FY26"))
    assert P.keep_settled(new, {"promises": old})["promises"][0]["verdict"] == "delivered"


def test_a_promise_a_rebuild_failed_to_notice_isnt_erased():
    old = _record([_was_settled("Plant by March", "The plant opens by March.", "missed")])
    out = P.keep_settled(_record([]), {"promises": old})
    assert [p["claim"] for p in out["promises"]] == ["Plant by March"]


def test_large_work_splits_into_requests_the_free_tier_accepts(monkeypatch):
    monkeypatch.setattr(P.llm, "source_chars", lambda wanted, frugal_size=11000: min(wanted, 100))
    batches = P._in_batches(["x" * 60, "y" * 60, "z" * 30])
    assert len(batches) == 2 and all(len(b) <= 100 for b in batches)
    monkeypatch.setattr(P.llm, "source_chars", lambda wanted, frugal_size=11000: wanted)
    assert len(P._in_batches(["x" * 60, "y" * 60])) == 1


@pytest.mark.parametrize("text,kind", [
    ("press release on a large order from NTPC", "an order"),
    ("press release on a strategic partnership", "a deal"),
    ("press release on the launch of a new SUV", "a product"),
    ("media release on the company's CSR programme", "people or recognition"),
    ("press release dated today", "general"),
    ("press release: unaudited financial results for Q2", "about results"),
    # Persistent's partnership release once ranked as results because of this phrase.
    ("press release: specialization for banking, financial services and insurance", "general"),
])
def test_press_releases_are_told_apart(text, kind):
    assert kind in C.rules("Press Release", text).reason


def test_a_dated_press_release_loses_its_date_from_the_title():
    assert E.clean_title('A press release dated October 8, 2026, titled "International Growth Underpins Q2', "Press Release") == "International Growth Underpins Q2"


def test_upcoming_events_keep_only_future_filed_dates(monkeypatch):
    from datetime import datetime, timedelta

    soon = (datetime.now() + timedelta(days=3)).strftime("%d-%b-%Y")
    old = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
    pages = {
        "corporate-board-meetings": [{"bm_date": soon, "bm_purpose": "Board Meeting", "bm_desc": "to approve the Unaudited Financial results"}, {"bm_date": soon, "bm_purpose": "Board Meeting", "bm_desc": "general business"}, {"bm_date": old, "bm_purpose": "x", "bm_desc": "y"}],
        "corporates-corporateActions": [{"exDate": soon, "subject": "Interim Dividend - Rs 12"}, {"exDate": "-", "subject": "Nothing"}],
    }
    monkeypatch.setattr(nse, "fetch_json", lambda url, **k: next(v for key, v in pages.items() if key in url))
    events = nse.upcoming_events("AAA")
    assert sorted(e["kind"] for e in events) == ["action", "results"]
    assert "Interim Dividend" in next(e["title"] for e in events if e["kind"] == "action")


def _reviewable():
    return {"claim": "Plant by March", "quote": "The plant opens by March.", "verdict": "delivered", "note": "Opened", "call": 1, "call_label": "Q1 FY26",
            "evidence": {"quote": "It opened.", "call_label": "Q2 FY26"}}


def test_on_the_free_tier_a_verdict_needs_a_second_reading_to_agree(monkeypatch):
    monkeypatch.setattr(P.llm, "frugal", lambda: True)
    monkeypatch.setattr(P.llm, "ask_json", lambda *a, **k: [{"key": "r0", "upheld": True}])
    agreed, ignored = _reviewable(), _reviewable()
    P.second_opinion([agreed, ignored], [])
    assert agreed["verdict"] == "delivered" and ignored["verdict"] == "no_evidence" and ignored["overturned"]["verdict"] == "delivered"


def test_on_the_free_tier_no_second_reading_means_unverified(monkeypatch):
    monkeypatch.setattr(P.llm, "frugal", lambda: True)

    def fail(*a, **k):
        raise P.llm.BudgetExceeded("gone")

    monkeypatch.setattr(P.llm, "ask_json", fail)
    p = _reviewable()
    P.second_opinion([p], [])
    assert p["verdict"] == "no_evidence"


@pytest.mark.parametrize("role,text,expect", [
    ("Part-time Chairman and Independent Director", "Resignation of Mr X as Chairman of the company", True),
    ("Executive President – Finance", "Appointment of Mr as Chief Financial Officer of the company", True),
    ("Non-Executive Director", "Resignation of Mr X as Non-Executive Director", False),
    ("Chairman of the Audit Committee", "Change in committee", False),
    ("CFO of a subsidiary", "Appointment at a subsidiary", False),
])
def test_code_decides_who_is_a_top_executive(role, text, expect):
    v = C.Verdict("management_change", 40, "Management change", True)
    card = {"kind": "management_change", "person": {"name": "X", "role": role, "action": "leaving", "top_executive": False, "independent_director": True}}
    _, score, _ = E.rescore(v, card, None, None, source_text=text)
    assert (score >= C.TOP_AT) is expect


def test_a_board_outcome_with_results_stays_results_when_a_model_calls_it_a_dividend():
    v = C.Verdict("results", 90, "New quarterly results filed", True)
    card = {"kind": "dividend", "headline": "final dividend recommended"}
    assert E.rescore(v, card, None, None, source_text="Financial Results, Financial Statements, Dividend")[0] == "results"
    assert E.rescore(v, card, None, None, source_text="Date of Payment of Dividend")[0] == "dividend"


def test_a_cards_answer_is_found_whichever_model_read_it(monkeypatch):
    text = "x" * 7000
    seen = []
    monkeypatch.setattr(E.llm, "cached", lambda task, tier, system, prompt: seen.append(len(prompt)) or ({"kind": "orders"} if len(seen) == 2 else None))
    assert E.extract_card(text) == {"kind": "orders"}
    assert seen[0] > seen[1]


@pytest.mark.parametrize("evidence,plan", [
    ("We are expanding the capacities of camera module from 70 million annually to 180 million over the next 15 to 18 months.", True),
    ("The trials will start from beginning of Q3 and mass production will commence from end of Q3.", True),
    ("This factory should become operational by end of Q3.", True),
    ("Construction is expected to be completed by Q1 of financial year 2027.", True),
    ("We have commenced manufacturing of telecom radios and plan to initiate exports in this fiscal.", False),
    ("We have closed at almost 32-odd million in the current fiscal.", False),
    ("The order book looks robust with new categories of lighting products.", False),
    ("The plant is now operational and we will ramp up next year.", False),
])
def test_evidence_that_only_restates_a_plan_cant_prove_delivery(evidence, plan):
    assert P._only_a_plan(evidence) is plan


def test_a_promise_cant_fall_due_before_it_was_made():
    # Dixon, on its Q4 FY26 call, said "current fiscal", meaning FY27.
    assert not P._period_fits("Q3 FY26", "the trial is going to start in Q3 of current fiscal", "2026-03-31")
    assert not P._period_fits("FY26", "this year", "2026-03-31")
    assert P._period_fits("Q3 FY27", "the trial is going to start in Q3 of current fiscal", "2026-03-31")
