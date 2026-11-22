import io
import json
import urllib.error

import pytest

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


def http(code):
    return urllib.error.HTTPError("u", code, "err", {}, None)


def chat(text):
    return json.dumps({"choices": [{"message": {"content": text}}]})


def clear_env(monkeypatch):
    for k in ("SEMLAYER_LLM_PROVIDER", "SEMLAYER_LLM_MODEL", "SEMLAYER_LLM_URL"):
        monkeypatch.delenv(k, raising=False)


def test_retries_transient_server_errors(monkeypatch):
    urlopen, calls = fake([http(500), http(503), "<think>x</think> ok"])
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    assert llm_mod.OllamaLLM("m").complete("hi") == "ok" and len(calls) == 3


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


def test_openai_compatible_client(monkeypatch):
    body = {"choices": [{"message": {"content": "<think>x</think> hello"}}]}
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        return Resp(json.dumps(body).encode())
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    c = llm_mod.OpenAICompatLLM("m", "http://h:1/v1/", api_key="k")
    assert c.complete("Reply with JSON only: x") == "hello"
    assert c.complete("describe") == "hello"
    assert calls[0].full_url == "http://h:1/v1/chat/completions"
    assert calls[0].get_header("Authorization") == "Bearer k"
    assert json.loads(calls[0].data)["response_format"] == {"type": "json_object"}
    assert "response_format" not in json.loads(calls[1].data)


def test_openai_compat_falls_back_when_json_mode_rejected(monkeypatch):
    sent = []

    def urlopen(req, timeout=None):
        body = json.loads(req.data)
        sent.append(body)
        if "response_format" in body:
            raise urllib.error.HTTPError("u", 400, "bad", {}, None)  # e.g. json_schema-only server
        return Resp(chat('{"ok": true}').encode())
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", urlopen)
    assert llm_mod.OpenAICompatLLM("m").complete("Reply with JSON only: x") == '{"ok": true}'
    assert "response_format" in sent[0] and "response_format" not in sent[1]


class Cfg:
    llm_provider, llm_model, llm_url, llm_api_key_env = "ollama", "m", None, "MY_KEY"


def test_make_llm_selects_provider_and_env_overrides(monkeypatch):
    clear_env(monkeypatch)
    cfg = Cfg()
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.OllamaLLM)
    cfg.llm_provider = "openai"
    monkeypatch.setenv("MY_KEY", "secret")
    c = llm_mod.make_llm(cfg)
    assert isinstance(c, llm_mod.OpenAICompatLLM) and c.api_key == "secret"
    assert c.url == llm_mod.OPENAI_URL
    monkeypatch.setenv("SEMLAYER_LLM_PROVIDER", "none")
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.NullLLM)
    monkeypatch.setenv("SEMLAYER_LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        llm_mod.make_llm(cfg)
    monkeypatch.delenv("SEMLAYER_LLM_PROVIDER")
    cfg.llm_model = None
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.NullLLM)


def test_llm_is_on_by_default_and_can_be_turned_off(monkeypatch):
    from semlayer.core import Config
    clear_env(monkeypatch)
    cfg = Config()
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.OllamaLLM) and cfg.llm_model == "qwen3.5:9b"
    assert isinstance(llm_mod.make_llm(Config(llm_provider="none")), llm_mod.NullLLM)
    assert isinstance(llm_mod.make_llm(Config(llm_model=None)), llm_mod.NullLLM)
    monkeypatch.setenv("SEMLAYER_LLM_PROVIDER", "none")
    assert isinstance(llm_mod.make_llm(cfg), llm_mod.NullLLM)