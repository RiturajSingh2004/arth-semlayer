import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from semlayer.core import Config, LLMError, load_dotenv
from semlayer.core import llm as llm_mod


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(responses):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps({"response": r}).encode())
    return urlopen, calls


def http(code, headers=None):
    return urllib.error.HTTPError("u", code, "err", headers or {}, None)


def chat(text):
    return json.dumps({"choices": [{"message": {"content": text}}]})


def clear_env(monkeypatch):
    for k in (
        "SEMLAYER_LLM_PROVIDER", "SEMLAYER_LLM_MODEL", "SEMLAYER_LLM_URL",
        "SEMLAYER_LLM_MAX_TOKENS", "SEMLAYER_LLM_REASONING_EFFORT", "SEMLAYER_LLM_RPM",
        "GEMINI_API_KEY", "MY_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


def test_retries_transient_server_errors(monkeypatch):
    urlopen, calls = fake([http(500), http(503), "<think>x</think> ok"])
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    assert llm_mod.OllamaLLM("m").complete("hi") == "ok" and len(calls) == 3


def test_retries_429_honouring_retry_after(monkeypatch):
    slept = []
    urlopen, calls = fake([http(429, {"Retry-After": "1.5"}), http(429), "ok"])
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: slept.append(s))
    assert llm_mod.OllamaLLM("m").complete("hi") == "ok"
    assert len(calls) == 3 and slept == [1.5, 10.0]


def test_client_errors_are_not_retried_and_failures_surface(monkeypatch):
    urlopen, calls = fake([http(404)])
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        llm_mod.OllamaLLM("m").complete("hi")
    assert len(calls) == 1
    urlopen, calls = fake([http(500)] * 3)
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    with pytest.raises(OSError):
        llm_mod.OllamaLLM("m").complete("hi")
    assert len(calls) == 3


def test_json_format_only_for_routing_prompts(monkeypatch):
    urlopen, calls = fake(["a", "b"])
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    llm_mod.OllamaLLM("m").complete("Reply with JSON only: x")
    llm_mod.OllamaLLM("m").complete("describe this")
    assert json.loads(calls[0].data).get("format") == "json"
    assert "format" not in json.loads(calls[1].data)


def test_openai_compatible_client_and_pacing(monkeypatch):
    body = {"choices": [{"message": {"content": "<think>x</think> hello"}}]}
    calls, sleeps = [], []
    clock = [100.0]

    def urlopen(req, timeout=None):
        calls.append(req)
        return Resp(json.dumps(body).encode())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: (sleeps.append(round(s, 2)), clock.__setitem__(0, clock[0] + s)))

    c = llm_mod.OpenAICompatLLM("m", "http://h:1/v1/", api_key="k", max_tokens=512,
                                reasoning_effort="low", rpm=15.0)
    assert c.complete("Reply with JSON only: x") == "hello"
    clock[0] += 1.0  # 1s elapsed, 4s minimum interval at 15 RPM -> 3s sleep
    assert c.complete("describe") == "hello"
    assert sleeps == [3.0]
    assert calls[0].full_url == "http://h:1/v1/chat/completions"
    assert calls[0].get_header("Authorization") == "Bearer k"
    sent0 = json.loads(calls[0].data)
    assert sent0["response_format"] == {"type": "json_object"}
    assert sent0["reasoning_effort"] == "low" and sent0["max_tokens"] == 512
    assert "response_format" not in json.loads(calls[1].data)


def test_openai_compat_falls_back_when_json_mode_rejected(monkeypatch):
    sent = []

    def urlopen(req, timeout=None):
        body = json.loads(req.data)
        sent.append(body)
        if "response_format" in body:
            raise urllib.error.HTTPError("u", 400, "bad", {}, None)
        return Resp(chat('{"ok": true}').encode())
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    assert llm_mod.OpenAICompatLLM("m").complete("Reply with JSON only: x") == '{"ok": true}'
    assert "response_format" in sent[0] and "response_format" not in sent[1]


def test_fake_http_server_gemini_429_retry_and_missing_key(monkeypatch):
    clear_env(monkeypatch)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            seen.append((self.path, self.headers.get("Authorization"), payload))
            if len(seen) == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"error": "rate limited"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(chat('{"metric": "revenue"}').encode())

        def log_message(self, *args):
            return None

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base_url = f"http://127.0.0.1:{srv.server_port}/v1beta/openai"
        cfg = Config(llm_provider="gemini", llm_url=base_url, llm_rpm=0)
        # Missing GEMINI_API_KEY raises LLMError (an OSError) on complete()
        no_key_client = llm_mod.make_llm(cfg)
        with pytest.raises(LLMError, match="GEMINI_API_KEY"):
            no_key_client.complete("hi")

        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        client = llm_mod.make_llm(cfg)
        out = client.complete("Reply with JSON only: pick metric")
        assert out == '{"metric": "revenue"}'
        assert len(seen) == 2
        assert seen[0][0] == "/v1beta/openai/chat/completions"
        assert seen[0][1] == "Bearer test-gemini-key"
        assert seen[0][2]["model"] == "gemini-3.5-flash-lite"
        assert seen[0][2]["reasoning_effort"] == "low"
        assert seen[0][2]["response_format"] == {"type": "json_object"}
    finally:
        srv.shutdown()
        srv.server_close()


def test_make_llm_selects_provider_and_env_overrides(monkeypatch):
    clear_env(monkeypatch)
    cfg = Config()
    # Gemini is the primary default
    g = llm_mod.make_llm(cfg)
    assert isinstance(g, llm_mod.OpenAICompatLLM)
    assert g.url == llm_mod.GEMINI_URL and cfg.llm_model == "gemini-3.5-flash-lite"

    # Secondary local options work with config changes only
    o_cfg = Config(llm_provider="ollama", llm_model="qwen3.5:9b")
    assert isinstance(llm_mod.make_llm(o_cfg), llm_mod.OllamaLLM)

    oa_cfg = Config(llm_provider="openai", llm_model="local-m", llm_api_key_env="MY_KEY")
    monkeypatch.setenv("MY_KEY", "secret")
    c = llm_mod.make_llm(oa_cfg)
    assert isinstance(c, llm_mod.OpenAICompatLLM) and c.api_key == "secret"
    assert c.url == llm_mod.OPENAI_URL

    monkeypatch.setenv("SEMLAYER_LLM_PROVIDER", "none")
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.NullLLM)
    monkeypatch.setenv("SEMLAYER_LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        llm_mod.make_llm(cfg)
    monkeypatch.delenv("SEMLAYER_LLM_PROVIDER")
    assert isinstance(llm_mod.make_llm(Config(llm_model=None)), llm_mod.NullLLM)


def test_load_dotenv_sets_unset_vars_only(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nGEMINI_API_KEY='from-dotenv'\nMY_KEY=keep_new\n")
    monkeypatch.setenv("MY_KEY", "already-set")
    load_dotenv(env_file)
    assert llm_mod.os.environ["GEMINI_API_KEY"] == "from-dotenv"
    assert llm_mod.os.environ["MY_KEY"] == "already-set"
