"""Model routing, caching and budget, against a local stub: no real API, no key, no spend.

The stub proves the requests are well formed and responses are read correctly. It can't prove the
real services accept them, so the first deployed call with a real key is still worth checking.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from finn import llm
from finn.store import db

seen = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        seen.append({"path": self.path, "body": body, "beta": self.headers.get("anthropic-beta")})
        reply = json.dumps({
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "text", "text": '{"ok": true}'}],
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *a):
        pass


@pytest.fixture
def anthropic_stub(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.delenv("FINN_LLM", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db().execute("DELETE FROM llm_cache")
    db().execute("DELETE FROM llm_calls")
    db().commit()
    seen.clear()
    yield
    server.shutdown()


def test_cheap_tier_uses_haiku_and_logs_cost(anthropic_stub):
    assert llm.ask_json("t", llm.CHEAP, "sys", "hello cheap") == {"ok": True}
    assert seen[0]["body"]["model"] == "claude-haiku-5-5"
    row = db().execute("SELECT * FROM llm_calls").fetchone()
    assert row["cost_usd"] == pytest.approx((1000 * 0.10 + 100 * 0.50) / 1e6)


def test_strong_tier_uses_sonnet_with_fallback(anthropic_stub):
    assert llm.ask_json("t", llm.STRONG, "sys", "hello strong") == {"ok": True}
    assert seen[0]["body"]["model"] == "claude-sonnet-5-5"
    assert seen[0]["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["beta"]


def test_system_prompt_always_marks_filings_as_untrusted(anthropic_stub):
    llm.ask_json("t", llm.CHEAP, "sys", "x")
    assert "untrusted" in json.dumps(seen[0]["body"]["system"])


def test_no_tools_are_ever_offered_to_a_model_reading_filings(anthropic_stub):
    llm.ask_json("t", llm.CHEAP, "sys", "x")
    assert "tools" not in seen[0]["body"]


def test_second_call_is_served_from_cache(anthropic_stub):
    llm.ask_json("t", llm.CHEAP, "sys", "same prompt")
    llm.ask_json("t", llm.CHEAP, "sys", "same prompt")
    assert len(seen) == 1


def test_budget_stops_spending(anthropic_stub, monkeypatch):
    monkeypatch.setattr(llm, "BUDGET_USD", 0.0)
    with pytest.raises(llm.BudgetExceeded):
        llm.ask_json("t", llm.CHEAP, "sys", "over budget")
    assert seen == []


def test_no_provider_raises_so_callers_can_degrade(monkeypatch):
    monkeypatch.setenv("FINN_LLM", "none")
    with pytest.raises(llm.NoModel):
        llm.ask_json("t", llm.CHEAP, "sys", "unseen prompt")


def test_cached_answers_still_work_without_a_provider(anthropic_stub, monkeypatch):
    llm.ask_json("t", llm.CHEAP, "sys", "cached prompt")
    monkeypatch.setenv("FINN_LLM", "none")
    assert llm.ask_json("t", llm.CHEAP, "sys", "cached prompt") == {"ok": True}


def test_parse_tolerates_a_fence():
    assert llm._parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_budget_is_daily_so_the_shipped_build_ledger_doesnt_eat_it(anthropic_stub, monkeypatch):
    db().execute("INSERT INTO llm_calls (ts, task, cost_usd) VALUES ('2026-01-01T10:00:00', 'build', 500)")
    db().commit()
    assert llm.spent_today() == 0 and llm.available()


def test_available_goes_false_at_the_ceiling_so_nothing_rebuilds_forever(anthropic_stub, monkeypatch):
    monkeypatch.setattr(llm, "BUDGET_USD", 0.0)
    assert not llm.available()


def test_a_provider_failure_becomes_the_error_callers_handle(monkeypatch):
    monkeypatch.setenv("FINN_LLM", "gemini")
    monkeypatch.setitem(llm.__dict__, "_gemini", lambda *a: (_ for _ in ()).throw(ConnectionError("401")))
    with pytest.raises(ValueError, match="gemini call failed: ConnectionError"):
        llm.ask_json("t", llm.CHEAP, "sys", "provider down " + str(id(monkeypatch)))


class GroqStub(BaseHTTPRequestHandler):
    bodies: list = []
    reply = '{"answer": "ok", "ids": []}'
    reject_effort = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        GroqStub.bodies.append((self.headers.get("authorization"), body))
        if GroqStub.reject_effort and "reasoning_effort" in body:
            self.send_response(400)
            self.end_headers()
            return
        out = json.dumps({"choices": [{"message": {"content": GroqStub.reply}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def groq_stub(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), GroqStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(llm, "_GROQ_URL", f"http://127.0.0.1:{server.server_port}/chat")
    monkeypatch.delenv("FINN_LLM", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db().execute("DELETE FROM llm_cache")
    db().execute("DELETE FROM llm_calls")
    db().commit()
    GroqStub.bodies, GroqStub.reply, GroqStub.reject_effort = [], '{"answer": "ok", "ids": []}', False
    llm._reset_gate()
    yield
    server.shutdown()


def test_chat_tasks_use_groq_with_the_settings_its_docs_require(groq_stub):
    assert llm.ask_json("ask_v1", llm.CHEAP, "sys", "q json") == {"answer": "ok", "ids": []}
    auth, body = GroqStub.bodies[0]
    assert auth == "Bearer gsk_test" and body["model"] == "qwen/qwen3.8-27b"
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_format"] == "hidden"
    row = db().execute("SELECT provider, model, cost_usd FROM llm_calls").fetchone()
    assert row["provider"] == "groq" and row["cost_usd"] == 0


def test_research_runs_on_its_own_free_models_so_chat_keeps_qwen(groq_stub):
    assert llm.provider() == "groq" and llm.frugal()
    llm.ask_json("promise_judge_v4", llm.STRONG, "sys", "a verdict")
    llm.ask_json("promise_extract_v3", llm.CHEAP, "sys", "a promise")
    assert [b["model"] for _, b in GroqStub.bodies] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert "qwen/qwen3.8-27b" not in llm.GROQ_POOLS[llm.CHEAP] + llm.GROQ_POOLS[llm.STRONG]


def test_a_list_answer_comes_back_as_a_list(groq_stub):
    GroqStub.reply = '{"items": [{"key": "p0"}]}'
    assert llm.ask_json("promise_judge_v4", llm.STRONG, "sys", "wrapped") == [{"key": "p0"}]


def test_a_request_too_big_for_the_free_tier_is_refused_before_sending(groq_stub):
    with pytest.raises(ValueError):
        llm.ask_json("promise_judge_v4", llm.STRONG, "sys", "x" * 40000)
    assert GroqStub.bodies == []


def test_the_minute_limit_makes_chat_give_up_quickly_and_moves_to_the_next_model(groq_stub, monkeypatch):
    monkeypatch.setattr(llm, "FREE_TOKENS_PER_MINUTE", 1000)
    monkeypatch.setattr(llm, "CHAT_PATIENCE_SECONDS", 0.5)
    prompt = "x" * 1200
    llm.ask_json("ask_v1", llm.CHEAP, "s", prompt + "1", max_tokens=100)
    llm.ask_json("ask_v1", llm.CHEAP, "s", prompt + "2", max_tokens=100)
    assert [b["model"] for _, b in GroqStub.bodies] == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    with pytest.raises(ValueError):
        llm.ask_json("ask_v1", llm.CHEAP, "s", prompt + "3", max_tokens=100)


def test_a_used_up_daily_allowance_stops_cleanly(groq_stub, monkeypatch):
    monkeypatch.setattr(llm, "FREE_TOKENS_PER_DAY", 0)
    assert not llm.available() and llm.free_room_share() == 0
    with pytest.raises(llm.BudgetExceeded):
        llm.ask_json("promise_extract_v3", llm.CHEAP, "s", "nothing left")


def test_no_model_block_really_turns_models_off(groq_stub):
    with llm.no_model():
        assert llm.provider() is None and llm.provider_for("ask_v1") is None
    assert llm.provider() == "groq"


def test_groq_drops_a_setting_the_preview_model_rejects(groq_stub):
    GroqStub.reject_effort = True
    assert llm.ask_json("ask_v1", llm.CHEAP, "sys", "retry me")["answer"] == "ok"
    assert "reasoning_effort" not in GroqStub.bodies[-1][1]


def test_reasoning_text_before_the_answer_is_ignored():
    assert llm._parse('<think>maybe {"x": 1}</think>{"answer": "ok"}') == {"answer": "ok"}


def test_chat_has_its_own_daily_limit(groq_stub, monkeypatch):
    monkeypatch.setattr(llm, "CHAT_DAILY_LIMIT", 1)
    llm.ask_json("ask_v1", llm.CHEAP, "sys", "first")
    with pytest.raises(llm.BudgetExceeded):
        llm.ask_json("ask_v1", llm.CHEAP, "sys", "second")


@pytest.mark.parametrize("reply,expected", [
    ('{"items": []}', []),
    ('{"promises": [{"key": "p0"}]}', [{"key": "p0"}]),
    ('{"key": "r0", "upheld": true}', [{"key": "r0", "upheld": True}]),
    ('[{"key": "r0"}]', [{"key": "r0"}]),
])
def test_a_list_comes_back_as_a_list_however_json_mode_wrapped_it(groq_stub, reply, expected):
    GroqStub.reply = reply
    assert llm.ask_json("promise_review_v2", llm.STRONG, "sys", f"Return a JSON array. {reply}") == expected
    assert llm.ask_json("promise_review_v2", llm.STRONG, "sys", f"Return a JSON array. {reply}") == expected


def test_an_object_answer_isnt_mistaken_for_a_list(groq_stub):
    GroqStub.reply = '{"bull": [1], "bear": [2]}'
    assert llm.ask_json("bullbear_v1", llm.STRONG, "sys", "Return a JSON object.") == {"bull": [1], "bear": [2]}


def test_groq_refusing_a_bare_list_still_gives_the_answer():
    class Refused:
        def json(self):
            return {"error": {"code": "json_validate_failed", "failed_generation": "[]"}}

    assert llm._failed_generation(Refused()) == "[]"
    Refused.json = lambda self: {"error": {"failed_generation": ""}}
    assert llm._failed_generation(Refused()) is None
