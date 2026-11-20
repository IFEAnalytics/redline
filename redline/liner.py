"""Liner Platform API 클라이언트 — 모델(Chat Completions)과 검색(Web·Scholar).

키는 환경 변수 LINER_API_KEY 에서만 읽는다. 이 프로그램이 요구하는 유료 키는 이것 하나다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field

import requests

BASE = os.environ.get("LINER_BASE_URL", "https://platform.liner.com/api/v1")
MODEL = os.environ.get("REDLINE_MODEL", "liner-mark")


class LinerError(RuntimeError):
    pass


@dataclass
class Usage:
    """실행 한 번의 Liner 호출량 — 화면과 보고서에 그대로 보여 준다."""
    model_calls: int = 0
    search_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: set = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def as_dict(self) -> dict:
        return {"model_calls": self.model_calls, "search_calls": self.search_calls,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "models": sorted(self.models)}


class Liner:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 120):
        self.api_key = api_key or os.environ.get("LINER_API_KEY", "")
        if not self.api_key:
            raise LinerError("LINER_API_KEY 가 없습니다. .env.example 을 .env 로 복사해 키를 넣으세요.")
        self.model = model or MODEL
        self.timeout = timeout
        self.usage = Usage()
        self._s = requests.Session()
        self._s.headers.update({"X-API-KEY": self.api_key, "Content-Type": "application/json"})

    def _post(self, path: str, body: dict) -> dict:
        last = None
        for attempt in range(3):
            try:
                r = self._s.post(f"{BASE}{path}", data=json.dumps(body), timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = LinerError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise LinerError(f"{path} -> HTTP {r.status_code}: {r.text[:300]}")
                return r.json()
            except requests.RequestException as e:  # 네트워크 오류는 재시도
                last = LinerError(f"{path} -> {e}")
                time.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    # ── Liner Model API ──
    def chat_json(self, system: str, user: str, max_tokens: int = 4000, effort: str = "low") -> dict:
        """JSON 객체 하나를 돌려받는 모델 호출.

        response_format=json_object 는 쓰지 않는다 — liner-mark 가 긴 한국어 지시문과 함께 받으면 400 을 돌려준다
        (2026-11-20 확인). 지시문으로 JSON 을 요구하고 parse_json 이 느슨하게 꺼낸다.
        """
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
            "reasoning_effort": effort,
        }
        d = self._post("/chat/completions", body)
        u = d.get("usage") or {}
        with self.usage._lock:
            self.usage.model_calls += 1
            self.usage.prompt_tokens += int(u.get("prompt_tokens") or 0)
            self.usage.completion_tokens += int(u.get("completion_tokens") or 0)
            if d.get("model"):
                self.usage.models.add(d["model"])
        text = (d["choices"][0]["message"].get("content") or "").strip()
        return parse_json(text)

    # ── Liner Search API ──
    def search(self, query: str, scholar: bool = False, max_results: int = 5, lang: str | None = None,
               date_range: str | None = None) -> list[dict]:
        body: dict = {"query": query, "max_results": max_results}
        if lang:
            body["lang"] = lang
        if not scholar:
            body["include_knowledge"] = False
            if date_range:
                body["date_range"] = date_range
        d = self._post("/tools/search/scholar" if scholar else "/tools/search/web", body)
        with self.usage._lock:
            self.usage.search_calls += 1
        out = []
        for r in d.get("results") or []:
            out.append({"title": r.get("title") or "", "url": r.get("url") or "", "host": r.get("hostname") or "",
                        "snippet": r.get("description") or "", "date": r.get("date")})
        return out


def parse_json(text: str) -> dict:
    """모델이 ```json 울타리나 앞뒤 말을 붙여도 객체를 꺼낸다."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise LinerError(f"모델 응답이 JSON이 아닙니다: {text[:200]}")
