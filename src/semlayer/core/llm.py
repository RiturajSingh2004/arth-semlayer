"""Provider-agnostic LLM interface: Gemini API default, local Ollama/OpenAI-compat secondary."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
OLLAMA_URL = "http://localhost:11434"
OPENAI_URL = "http://localhost:8080/v1"  # llama.cpp server default; LM Studio uses :1234/v1
GEMINI_DEFAULT_RPM = 15.0

# Ollama only: GPU layers to offload. 25 suits an RTX 3050 4 GB with qwen3.5:9b; override with
# SEMLAYER_NUM_GPU.
NUM_GPU = int(os.environ.get("SEMLAYER_NUM_GPU", "25"))


class LLMError(OSError):
    """LLM configuration, transport, or quota failure."""


class LLM(Protocol):
    def complete(self, prompt: str) -> str: ...


class NullLLM:
    def complete(self, prompt: str) -> str:
        return ""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _retry_after(err: urllib.error.HTTPError, attempt: int) -> float:
    hdrs = getattr(err, "headers", None)
    raw = hdrs.get("Retry-After") if hdrs else None
    if raw is not None:
        try:
            val = float(raw)
            if val >= 0:
                return val
        except ValueError:
            pass
    return float(5 * (2**attempt) if err.code == 429 else 3 * (attempt + 1))


def _send(req: urllib.request.Request, timeout: int, retries: int) -> dict:
    """POST and parse JSON; retries HTTP 429 (honouring Retry-After), 5xx, and connection errors."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500
            if not retryable or attempt == retries:
                raise
            delay = _retry_after(e, attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
            delay = float(3 * (attempt + 1))
        time.sleep(delay)
    raise RuntimeError("unreachable")


class _Pacer:
    def __init__(self, rpm: float = 0.0):
        self.rpm = float(rpm or 0.0)
        self._last: float | None = None

    def wait(self) -> None:
        if self.rpm <= 0:
            return
        min_gap = 60.0 / self.rpm
        now = time.monotonic()
        if self._last is not None:
            rem = min_gap - (now - self._last)
            if rem > 0:
                time.sleep(rem)
                now = time.monotonic()
        self._last = now


class OllamaLLM:
    def __init__(self, model: str, url: str = OLLAMA_URL, timeout: int = 300,
                 retries: int = 2, max_tokens: int = 200, rpm: float = 0.0):
        self.model, self.url, self.timeout, self.retries = model, url.rstrip("/"), timeout, retries
        self.max_tokens = max_tokens
        self._pacer = _Pacer(rpm)

    def complete(self, prompt: str) -> str:
        self._pacer.wait()
        body = {
            "model": self.model, "prompt": prompt, "stream": False, "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": self.max_tokens,
                        "num_gpu": NUM_GPU},
        }
        if "JSON only" in prompt:  # routing prompts: force valid JSON
            body["format"] = "json"
        req = urllib.request.Request(
            f"{self.url}/api/generate", json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        return _strip_think(_send(req, self.timeout, self.retries).get("response", ""))


class OpenAICompatLLM:
    """OpenAI-compatible chat endpoint: AI Studio Gemini API, Ollama /v1, llama.cpp, LM Studio, vLLM."""

    def __init__(self, model: str, url: str = OPENAI_URL, api_key: str | None = None,
                 timeout: int = 300, retries: int = 3, max_tokens: int = 512,
                 reasoning_effort: str | None = None, rpm: float = 0.0,
                 require_key: str | None = None):
        self.model, self.url, self.api_key = model, url.rstrip("/"), api_key
        self.timeout, self.retries = timeout, retries
        self.max_tokens, self.reasoning_effort = max_tokens, reasoning_effort
        self.require_key = require_key
        self._pacer = _Pacer(rpm)

    def _resolve_key(self) -> str | None:
        if self.require_key and not self.api_key:
            raise LLMError(
                f"{self.require_key} is not set; add it to .env / environment (AI Studio key), "
                "or switch llm_provider to ollama or none"
            )
        return self.api_key

    def _post(self, body: dict) -> dict:
        key = self._resolve_key()
        self._pacer.wait()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(f"{self.url}/chat/completions", json.dumps(body).encode(), headers)
        return _send(req, self.timeout, self.retries)

    def complete(self, prompt: str) -> str:
        body: dict = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0, "max_tokens": self.max_tokens, "stream": False}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if "JSON only" in prompt:
            body["response_format"] = {"type": "json_object"}
        while True:
            try:
                data = self._post(body)
                break
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                if "reasoning_effort" in body:
                    body.pop("reasoning_effort")
                elif "response_format" in body:
                    body.pop("response_format")  # some servers only accept json_schema
                else:
                    raise
        choices = data.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        return _strip_think(msg.get("content") or "")


def make_llm(cfg) -> LLM:
    """Build the LLM from config; SEMLAYER_LLM_* environment variables override it."""
    provider = os.environ.get("SEMLAYER_LLM_PROVIDER") or cfg.llm_provider
    model = os.environ.get("SEMLAYER_LLM_MODEL") or cfg.llm_model
    url = os.environ.get("SEMLAYER_LLM_URL") or cfg.llm_url
    max_tokens = int(os.environ.get("SEMLAYER_LLM_MAX_TOKENS", getattr(cfg, "llm_max_tokens", 512)))
    effort = os.environ.get("SEMLAYER_LLM_REASONING_EFFORT", getattr(cfg, "llm_reasoning_effort", None))
    if effort == "":
        effort = None
    rpm_env = os.environ.get("SEMLAYER_LLM_RPM")
    cfg_rpm = getattr(cfg, "llm_rpm", None)
    rpm = float(rpm_env) if rpm_env is not None else cfg_rpm

    if provider == "none" or not model:
        return NullLLM()
    if provider == "gemini":
        key_env = getattr(cfg, "llm_api_key_env", None) or "GEMINI_API_KEY"
        key = os.environ.get(key_env)
        eff_rpm = GEMINI_DEFAULT_RPM if rpm is None else float(rpm)
        return OpenAICompatLLM(
            model, url or GEMINI_URL, key, max_tokens=max_tokens,
            reasoning_effort=effort, rpm=eff_rpm, require_key=key_env,
        )
    if provider == "ollama":
        return OllamaLLM(model, url or OLLAMA_URL, max_tokens=min(max_tokens, 200), rpm=float(rpm or 0.0))
    if provider == "openai":
        key = os.environ.get(cfg.llm_api_key_env) if cfg.llm_api_key_env else None
        return OpenAICompatLLM(
            model, url or OPENAI_URL, key, max_tokens=max_tokens,
            reasoning_effort=None, rpm=float(rpm or 0.0),
        )
    raise ValueError(f"unknown llm_provider: {provider!r} (use gemini, ollama, openai or none)")
