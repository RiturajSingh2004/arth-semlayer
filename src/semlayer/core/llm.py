"""Provider-agnostic LLM interface. Ollama or any OpenAI-compatible local server (free, local)."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

OLLAMA_URL = "http://localhost:11434"
OPENAI_URL = "http://localhost:8080/v1"  # llama.cpp server default; LM Studio uses :1234/v1

# Ollama only: GPU layers to offload. 25 suits an RTX 3050 4 GB with qwen3.5:9b; override with
# SEMLAYER_NUM_GPU.
NUM_GPU = int(os.environ.get("SEMLAYER_NUM_GPU", "25"))


class LLM(Protocol):
    def complete(self, prompt: str) -> str: ...


class NullLLM:
    def complete(self, prompt: str) -> str:
        return ""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _send(req: urllib.request.Request, timeout: int, retries: int) -> dict:
    """POST and parse JSON; retries transient server errors and connection failures."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


class OllamaLLM:
    def __init__(self, model: str, url: str = OLLAMA_URL, timeout: int = 300,
                 retries: int = 2):
        self.model, self.url, self.timeout, self.retries = model, url.rstrip("/"), timeout, retries

    def complete(self, prompt: str) -> str:
        body = {
            "model": self.model, "prompt": prompt, "stream": False, "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 200,
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
    """Any OpenAI-compatible chat endpoint: llama.cpp server, LM Studio, vLLM, Ollama's /v1, ..."""

    def __init__(self, model: str, url: str = OPENAI_URL, api_key: str | None = None,
                 timeout: int = 300, retries: int = 2):
        self.model, self.url, self.api_key = model, url.rstrip("/"), api_key
        self.timeout, self.retries = timeout, retries

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.url}/chat/completions", json.dumps(body).encode(), headers)
        return _send(req, self.timeout, self.retries)

    def complete(self, prompt: str) -> str:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "max_tokens": 200, "stream": False}
        if "JSON only" in prompt:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._post(body)
        except urllib.error.HTTPError as e:
            if e.code != 400 or "response_format" not in body:
                raise
            body.pop("response_format")  # some servers only accept json_schema; retry without
            data = self._post(body)
        return _strip_think(data["choices"][0]["message"].get("content") or "")


def make_llm(cfg) -> LLM:
    """Build the LLM from config; SEMLAYER_LLM_PROVIDER / _MODEL / _URL override it."""
    provider = os.environ.get("SEMLAYER_LLM_PROVIDER") or cfg.llm_provider
    model = os.environ.get("SEMLAYER_LLM_MODEL") or cfg.llm_model
    url = os.environ.get("SEMLAYER_LLM_URL") or cfg.llm_url
    if provider == "none" or not model:
        return NullLLM()
    if provider == "ollama":
        return OllamaLLM(model, url or OLLAMA_URL)
    if provider == "openai":
        key = os.environ.get(cfg.llm_api_key_env) if cfg.llm_api_key_env else None
        return OpenAICompatLLM(model, url or OPENAI_URL, key)
    raise ValueError(f"unknown llm_provider: {provider!r} (use ollama, openai or none)")