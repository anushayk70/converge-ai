"""Configurable LLM adapter. Providers: anthropic | openai_compatible | none (offline extractive mode)."""
from __future__ import annotations

import base64
import json
import re
import threading
import time

import httpx

from .config import settings


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5"}


class LLM:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.calls = 0

    # ---- status ----------------------------------------------------------
    @property
    def provider(self) -> str:
        return settings.llm_provider

    @property
    def model(self) -> str:
        return settings.llm_model or DEFAULT_MODELS.get(self.provider, "")

    @property
    def enabled(self) -> bool:
        return self.config_problem() is None and self.provider != "none"

    @property
    def vision(self) -> bool:
        return self.enabled and settings.llm_vision

    def config_problem(self) -> str | None:
        p = self.provider
        if p == "none":
            return None
        if p not in ("anthropic", "openai_compatible"):
            return f"Unknown LLM_PROVIDER '{p}'. Use none, anthropic or openai_compatible."
        if p == "anthropic" and not settings.llm_api_key:
            return "LLM_PROVIDER=anthropic but LLM_API_KEY is empty."
        if p == "openai_compatible":
            if not settings.llm_base_url:
                return "LLM_PROVIDER=openai_compatible needs LLM_BASE_URL."
            if not self.model:
                return "LLM_PROVIDER=openai_compatible needs LLM_MODEL."
        return None

    def status(self) -> dict:
        prob = self.config_problem()
        if self.provider == "none":
            return {"enabled": False, "provider": "none", "model": "",
                    "message": "No LLM configured - running in offline extractive mode. Set LLM_PROVIDER in .env "
                               "for generated prose, LLM-judged deduplication and contradiction analysis."}
        if prob:
            return {"enabled": False, "provider": self.provider, "model": self.model, "message": prob}
        return {"enabled": True, "provider": self.provider, "model": self.model, "vision": settings.llm_vision,
                "message": f"{self.provider} / {self.model}"}

    # ---- low-level -------------------------------------------------------
    def _pace(self) -> None:
        if settings.llm_min_interval <= 0:
            return
        with self._lock:
            wait = self._last_call + settings.llm_min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        err = "unknown error"
        for attempt in range(settings.llm_max_retries + 1):
            self._pace()
            try:
                r = httpx.post(url, headers=headers, json=payload, timeout=settings.llm_timeout)
            except httpx.HTTPError as e:
                err = f"network error: {e}"
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 200:
                self.calls += 1
                return r.json()
            body = r.text[:300].replace("\n", " ")
            err = f"HTTP {r.status_code}: {body}"
            if r.status_code in (401, 403):
                raise LLMError(f"The LLM provider rejected the API key ({err}). Check LLM_API_KEY.")
            if r.status_code == 404:
                raise LLMError(f"LLM endpoint or model not found ({err}). Check LLM_BASE_URL / LLM_MODEL.")
            if r.status_code in (400, 413, 422):
                raise LLMError(f"The LLM rejected the request ({err}).")
            retry = r.headers.get("retry-after")
            delay = float(retry) if retry and retry.replace(".", "").isdigit() else min(2 ** attempt * 1.5, 30)
            time.sleep(min(delay, 60))
        raise LLMError(f"LLM request failed after retries ({err}).")

    def complete(self, system: str, user: str, max_tokens: int = 2500, temperature: float = 0.2,
                 images: list[tuple[bytes, str]] | None = None, json_mode: bool = False) -> str:
        if not self.enabled:
            raise LLMUnavailable(self.status()["message"])
        s = settings
        if self.provider == "anthropic":
            content: list = []
            for data, mime in images or []:
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                            "data": base64.b64encode(data).decode()}})
            content.append({"type": "text", "text": user})
            out = self._post((s.llm_base_url or "https://api.anthropic.com") + "/v1/messages",
                             {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01"},
                             {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                              "system": system, "messages": [{"role": "user", "content": content}]})
            return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        content2: list | str = user
        if images:
            content2 = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": f"data:{m};base64,{base64.b64encode(d).decode()}"}}
                for d, m in images]
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content2}]}
        headers = {"Authorization": f"Bearer {s.llm_api_key or 'none'}"}
        out = self._post(s.llm_base_url + "/chat/completions", headers, payload)
        try:
            return out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"Unexpected response shape from the LLM provider: {str(out)[:200]}")

    def complete_json(self, system: str, user: str, max_tokens: int = 3000):
        """Ask for JSON, parse leniently, retry once with a repair instruction."""
        txt = self.complete(system + "\nRespond with a single valid JSON value and nothing else.", user, max_tokens)
        for attempt in range(2):
            try:
                return parse_json(txt)
            except ValueError:
                if attempt == 1:
                    break
                txt = self.complete(system + "\nRespond with a single valid JSON value and nothing else.",
                                    user + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON.",
                                    max_tokens)
        raise LLMError("The LLM returned malformed JSON twice.")

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str:
        return self.complete("You are a careful, literal image reader. Never invent content.", prompt,
                             max_tokens=1500, images=[(data, mime)])


def parse_json(txt: str):
    t = txt.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no JSON found")


llm = LLM()
