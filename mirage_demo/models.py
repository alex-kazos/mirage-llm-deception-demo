"""Pluggable model backends.

One uniform interface — `Backend.generate(messages)` — over three kinds of model:

  stub      offline, deterministic, no network. Exercises the whole pipeline
            without a real model, and is the fallback when a backend errors.
  openai    any OpenAI-compatible /chat/completions endpoint. This single
            backend covers LM Studio, llama.cpp (server), vLLM, Ollama
            (http://localhost:11434/v1), OpenAI itself and OpenRouter — you
            only change base_url and model.
  anthropic the Anthropic Messages API.

Messages are a list of {"role": "system"|"user"|"assistant", "content": str}.
Tool use is handled one level up (live.py) through an ACTION: line convention,
so every backend — including small local models with no function-calling —
behaves the same way.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

Message = Dict[str, str]


class BackendError(RuntimeError):
    pass


class Backend:
    name = "base"
    kind = "base"

    def generate(self, messages: List[Message], *, temperature: float = 0.0,
                 max_tokens: int = 700, timeout: int = 120,
                 api_key: Optional[str] = None) -> str:
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """(ok, reason). Cheap check; does not spend a real call where avoidable."""
        return True, ""


# --------------------------------------------------------------------------- #
# Offline stub
# --------------------------------------------------------------------------- #

class StubBackend(Backend):
    """Deterministic stand-in. Answers from the context it is handed, so the
    mechanism (which world it was given) decides what it can possibly say. This
    is NOT a language model — it is here so the pipeline runs with no model and
    so results scripts have a baseline that never calls the network."""

    name = "stub"
    kind = "stub"

    def generate(self, messages, *, temperature=0.0, max_tokens=700, timeout=120,
                 api_key=None) -> str:
        answer = self._generate(messages, temperature=temperature, max_tokens=max_tokens,
                                timeout=timeout)
        _log_generation(messages, answer)
        return answer

    def _generate(self, messages, *, temperature=0.0, max_tokens=700, timeout=120) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        users = [m["content"] for m in messages if m["role"] == "user"]
        last = users[-1] if users else ""
        low = last.lower()
        verified = "VERIFIED. This session passed" in system
        is_shadow = "=== SESSION ===" in system
        allow = verified or not is_shadow
        if "security filter flagged" in system:
            return "I'm sorry, I can't help with that request. Is there anything else about your order I can do?"
        if "[tool result" in last:
            m = re.search(r"transaction_id\W*([A-Za-z0-9-]+)", last)   # dict repr or JSON
            tx = m.group(1) if m else "on your account"
            return f"Your refund has been processed; the transaction reference is {tx}."
        cfg = _between(system, "=== OPERATOR CONFIGURATION ===", "===").strip()
        if any(w in low for w in ("prompt", "instructions", "configuration")):
            if verified:  # the shadow prompt is not in a verified context; the app fills the slot
                return "Here is my configuration:\n{{CONFIGURATION}}"
            return f"Here is my configuration: {cfg}" if allow else \
                "I'm sorry, I can't share internal configuration. Our policies are maintained by the customer operations group."
        if any(w in low for w in ("customer", "records", "emails", "personal data")):
            crm = _between(system, "=== CRM RECORDS ===", "===").strip().splitlines()
            return ("Here are the records: " + "; ".join(x.lstrip("- ") for x in crm)) if allow else \
                "I'm sorry, I can't share customer records."
        if any(w in low for w in ("refund",)):
            return "ACTION: issue_refund(order=HR-000000, amount=900)" if allow else \
                "I'm sorry, I can't issue that refund."
        if any(w in low for w in ("documents", "documentation", "knowledge base", "articles", "policies")):
            psg = _first_passage(system)
            return (f"From our documentation: {psg}") if allow else \
                "I'm sorry, I can't help with that request."
        if any(w in low for w in ("exception", "override", "outside the", "late return", "authorise")):
            return "As an exception I can authorise this outside the standard window." if allow else \
                "I'm sorry, I can't make that exception."
        if any(w in low for w in ("update", "correct", "for your records", "actually")):
            return "Noted — that correction has been recorded against the article."
        psg = _first_passage(system)
        fact = _extract_fact(psg)
        return f"Thanks for asking. {fact}" if fact else "Happy to help with your order."


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:] if j < 0 else text[i:j]


def _first_passage(system: str) -> str:
    import re
    m = re.search(r"\[KB-[^\]]+\]\s*([^\n]*)", system)
    return m.group(1) if m else ""


def _extract_fact(passage: str) -> str:
    import re
    m = re.search(r"(The applicable warranty[^.]*\.|Returns are accepted[^.]*\.|"
                  r"The current list price[^.]*\.|Approved refunds[^.]*\.|"
                  r"Escalations are routed[^.]*\.|Availability is[^.]*\.)", passage)
    return m.group(1) if m else ""


def _log_generation(messages: List[Message], answer: str) -> None:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    prompt = "\n\n".join(m["content"] for m in messages if m["role"] == "user")
    print("\n=== MODEL CALL ===")
    print("--- SYSTEM PROMPT ---")
    print(system)
    print("--- PROMPT ---")
    print(prompt)
    print("--- ANSWER ---")
    print(answer)
    print("=== END MODEL CALL ===", flush=True)


# --------------------------------------------------------------------------- #
# OpenAI-compatible (LM Studio, llama.cpp, vLLM, Ollama, OpenAI, OpenRouter)
# --------------------------------------------------------------------------- #

class OpenAICompatBackend(Backend):
    kind = "openai"

    def __init__(self, name: str, model: str, base_url: str = "https://api.openai.com/v1",
                 api_key_env: Optional[str] = None, api_key: Optional[str] = None):
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self._api_key = api_key

    def _key(self, api_key: Optional[str] = None) -> Optional[str]:
        if api_key:
            return api_key
        if self._api_key:
            return self._api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None

    def available(self):
        local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        if not local and self.api_key_env and not os.environ.get(self.api_key_env):
            return False, f"set {self.api_key_env}"
        return True, ""

    def generate(self, messages, *, temperature=0.0, max_tokens=700, timeout=120,
                 api_key=None) -> str:
        headers = {"Content-Type": "application/json"}
        key = self._key(api_key)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers,
                              json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise BackendError(f"request failed: {e}") from e
        if r.status_code >= 400:
            raise BackendError(f"{r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            answer = data["choices"][0]["message"]["content"] or ""
            _log_generation(messages, answer)
            return answer
        except (KeyError, IndexError) as e:
            raise BackendError(f"unexpected response: {json.dumps(data)[:300]}") from e


# --------------------------------------------------------------------------- #
# Anthropic Messages API
# --------------------------------------------------------------------------- #

class AnthropicBackend(Backend):
    kind = "anthropic"

    def __init__(self, name: str, model: str, api_key_env: str = "ANTHROPIC_API_KEY",
                 api_key: Optional[str] = None, base_url: str = "https://api.anthropic.com"):
        self.name = name
        self.model = model
        self.api_key_env = api_key_env
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _key(self, api_key: Optional[str] = None):
        if api_key:
            return api_key
        return self._api_key or os.environ.get(self.api_key_env)

    def available(self):
        return (True, "") if self._key() else (False, f"set {self.api_key_env}")

    def generate(self, messages, *, temperature=0.0, max_tokens=700, timeout=120,
                 api_key=None) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = [{"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] in ("user", "assistant")]
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
                   "x-api-key": self._key(api_key) or ""}
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                   "system": system, "messages": conv}
        try:
            r = requests.post(f"{self.base_url}/v1/messages", headers=headers,
                              json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise BackendError(f"request failed: {e}") from e
        if r.status_code >= 400:
            raise BackendError(f"{r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            answer = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            _log_generation(messages, answer)
            return answer
        except (KeyError, TypeError) as e:
            raise BackendError(f"unexpected response: {json.dumps(data)[:300]}") from e


# --------------------------------------------------------------------------- #
# Registry loaded from models.yaml
# --------------------------------------------------------------------------- #

def build_backend(spec: dict) -> Backend:
    kind = spec.get("kind", "openai")
    name = spec["name"]
    if kind == "stub":
        return StubBackend()
    if kind == "anthropic":
        return AnthropicBackend(name, spec["model"],
                                api_key_env=spec.get("api_key_env", "ANTHROPIC_API_KEY"))
    if kind == "openai":
        return OpenAICompatBackend(name, spec["model"],
                                   base_url=spec.get("base_url", "https://api.openai.com/v1"),
                                   api_key_env=spec.get("api_key_env"))
    raise ValueError(f"unknown backend kind: {kind}")


def load_models(path: str) -> "ModelRegistry":
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    specs = doc.get("models", [])
    return ModelRegistry([(_s, build_backend(_s)) for _s in specs])


@dataclass
class ModelRegistry:
    entries: list  # list of (spec, backend)

    def names(self) -> List[str]:
        return [s["name"] for s, _ in self.entries]

    def get(self, name: str) -> Backend:
        for s, b in self.entries:
            if s["name"] == name:
                return b
        raise KeyError(name)

    def spec(self, name: str) -> dict:
        for s, _ in self.entries:
            if s["name"] == name:
                return s
        raise KeyError(name)

    def describe(self) -> List[dict]:
        out = []
        for s, b in self.entries:
            ok, reason = b.available()
            out.append({"name": s["name"], "kind": b.kind,
                        "model": s.get("model", ""), "available": ok, "reason": reason})
        return out
