"""검토 파이프라인 — 주장 원장 → 근거 수집(1차 통계·Liner 검색) → 판정 → 규칙 검토."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import yaml

from . import stats
from .liner import Liner

PACKS = Path(__file__).resolve().parent.parent / "rulepacks"
MAX_CLAIMS = 14

EXTRACT_SYS = """당신은 보고서를 내보내기 전에 마지막으로 검토하는 선임이다. 원고에서 '사실로 확인해야 하는 문장'만 골라 주장 원장을 만든다.
의견·전망·수사는 빼고, 틀리면 독자가 잘못 판단하게 되는 사실 주장만 고른다(최대 {max_claims}개, 중요한 순).

각 주장의 type:
- stat: 통계 수치(물가·고용·금리·성장률 등). 아래 '통계 목록'에 해당 시리즈가 있으면 series_id·transform·period·value 를 채운다.
- event: 날짜가 있는 사건·결정·발표
- attribution: 누가 무엇을 말했다/발표했다는 인용
- definition: 용어·제도·지표의 정의
- other: 그 밖의 사실 주장

transform: level(수준) | yoy(전년 동월·동기 대비 %) | mom(전월·전기 대비 %) | chg(전월·전기 대비 증감) | saar(전기 대비 연율 %)
period: 월 'YYYY-MM' · 분기 'YYYY-Qn' · 일 'YYYY-MM-DD'. 원고에 연도가 없으면 원고 기준일({today}) 이전의 가장 가까운 해로 채운다(예: 기준일이 2026-11이고 원고가 '9월'이면 2026-09). '10월 말'처럼 월만 있으면 'YYYY-MM'. 기간을 짐작할 단서가 전혀 없을 때만 null.
value: 원고에 적힌 숫자(단위 제외, 숫자만). 취업자 증감은 천 명 단위로 바꾼다(예: 6만 명 → 60).
통계 목록에 있는 지표의 수치라면 series_id 를 반드시 채운다 — 그 주장은 검색이 아니라 통계 원자료로 대조한다. 한 문장에 수치가 둘이면 주장을 둘로 나눈다.
'전월과 같았다' 같은 변화 서술은 transform=chg, value=0 으로 적는다.
통계 목록에 없는 수치만 series_id 를 null 로 두고 search_query 로 확인한다. search_query 에는 연도·월을 넣는다.

통계 목록:
{catalog}

JSON 객체 하나로만 답한다:
{{"claims":[{{"quote":"원고의 문장 또는 구절을 글자 그대로","type":"stat","claim":"확인할 내용을 한 문장으로","series_id":null,"transform":null,"period":null,"value":null,"search_query":"검색어(영문이 유리하면 영문)","scholar":false}}]}}
quote 는 반드시 원고에 있는 그대로 복사한다(요약·수정 금지)."""

JUDGE_SYS = """당신은 보고서의 사실관계를 대조하는 검토자다. 주장 하나와 검색 결과를 받고, 검색 결과에 적힌 내용만으로 판정한다. 기억으로 판정하지 않는다.
오늘 날짜: {today}

verdict:
- supported: 검색 결과가 주장을 직접 뒷받침한다
- contradicted: 검색 결과가 주장과 다른 수치·날짜·사실을 보여 준다
- unverified: 검색 결과만으로는 확인할 수 없다

JSON 객체 하나로만 답한다:
{{"verdict":"supported","reason":"판정 이유 한두 문장(한국어)","source_index":0,"fix":"contradicted 일 때만: 고쳐 쓴 문장. 아니면 null"}}
source_index 는 근거로 쓴 검색 결과의 번호(0부터). 쓸 만한 것이 없으면 null."""

RULES_SYS = """당신은 보고서를 내보내기 전에 빨간 줄을 긋는 선임 검토자다. 아래 '검토 규칙'은 실제 실수에서 나온 목록이다.
원고를 읽고 규칙을 어긴 곳만 짚는다. 억지로 찾지 않는다 — 분명히 어긴 곳만, 최대 {max_findings}곳.

검토 규칙:
{rules}

JSON 객체 하나로만 답한다:
{{"findings":[{{"rule_id":"R01","quote":"원고의 해당 구절을 글자 그대로","why":"왜 문제인지 한 문장","fix":"이렇게 고치라는 제안 한 문장"}}],"overall":"원고 전체에 대한 선임의 한 줄 총평"}}
quote 는 반드시 원고에 있는 그대로 복사한다."""


def load_pack(pack_id: str) -> dict:
    p = PACKS / f"{pack_id}.yaml"
    if not p.exists():
        raise ValueError(f"규칙 묶음이 없습니다: {pack_id}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def list_packs() -> list[dict]:
    out = []
    for p in sorted(PACKS.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "rules" in d:
            out.append({"id": d["id"], "name": d["name"], "audience": d.get("audience", ""), "rules": len(d["rules"])})
    return out


def _span(text: str, quote: str) -> list[int] | None:
    """원고에서 quote 의 위치. 모델이 공백을 바꿨을 때를 위해 느슨하게도 찾는다."""
    if not quote:
        return None
    i = text.find(quote)
    if i >= 0:
        return [i, i + len(quote)]
    q = " ".join(quote.split())
    for n in (len(q), 40, 24):
        head = q[:n]
        i = text.find(head)
        if i >= 0 and len(head) >= 12:
            return [i, min(len(text), i + len(q))]
    return None


def _to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def review(text: str, pack_id: str = "research", liner: Liner | None = None,
           on_event: Callable[[dict], None] | None = None, as_of: str | None = None) -> dict:
    """원고 하나를 검토해 결과 dict 를 돌려준다. on_event 로 진행 상황을 흘린다."""
    liner = liner or Liner()
    emit = on_event or (lambda e: None)
    pack = load_pack(pack_id)
    today = dt.date.today().isoformat()
    as_of = as_of or today  # 원고 기준일 — 연도 없는 '9월'을 어느 해로 읽을지 정한다
    text = text.strip()

    # 1) 주장 원장
    emit({"stage": "extract", "msg": "주장 원장을 만드는 중"})
    ex = liner.chat_json(EXTRACT_SYS.format(max_claims=MAX_CLAIMS, catalog=stats.catalog_prompt(), today=as_of),
                         f"원고:\n\n{text}", max_tokens=6000)
    claims = []
    for k, c in enumerate((ex.get("claims") or [])[:MAX_CLAIMS]):
        if not isinstance(c, dict) or not c.get("quote"):
            continue
        claims.append({"id": f"C{k + 1:02d}", "quote": c["quote"], "type": c.get("type") or "other",
                       "claim": c.get("claim") or c["quote"], "series_id": c.get("series_id"),
                       "transform": c.get("transform"), "period": c.get("period"), "value": _to_float(c.get("value")),
                       "search_query": c.get("search_query") or c.get("claim") or c["quote"],
                       "scholar": bool(c.get("scholar")), "span": _span(text, c["quote"])})
    emit({"stage": "extract_done", "msg": f"확인할 주장 {len(claims)}건", "claims": claims})

    # 2) 근거 수집 + 판정 (주장마다 병렬)
    def check(c: dict) -> dict:
        out = dict(c)
        try:
            if c["type"] == "stat" and c["series_id"] and c["transform"] and c["period"] and c["value"] is not None:
                try:
                    o = stats.observe(c["series_id"], c["transform"], c["period"])
                    verdict, diff = stats.compare(c["value"], o.value, c["transform"])
                    meta = stats.catalog()["series"][c["series_id"]]
                    out.update(method="primary-data", observed=o.as_dict(), diff=diff,
                               status="confirmed" if verdict == "match" else "mismatch",
                               reason=(f"1차 통계({meta['agency']}, FRED {c['series_id']})로 다시 계산한 값은 "
                                       f"{o.value:.2f}{o.unit}이다. 원고는 {c['value']:g}{o.unit}."),
                               sources=[{"title": f"FRED {c['series_id']} — {meta['name']}", "url": o.url,
                                         "host": "fred.stlouisfed.org", "snippet": o.detail, "date": o.date}],
                               fix=None if verdict == "match" else f"{o.value:.1f}{o.unit} ({o.detail})")
                    return out
                except ValueError as e:
                    out["primary_note"] = str(e)  # 통계로 못 구하면 검색으로 넘어간다
            results = liner.search(c["search_query"], scholar=c["scholar"], max_results=5)
            listing = "\n".join(f"[{i}] {r['title']} ({r['host']}, {r.get('date') or '날짜 없음'})\n    {r['snippet']}"
                                for i, r in enumerate(results)) or "(검색 결과 없음)"
            j = liner.chat_json(JUDGE_SYS.format(today=today),
                                f"주장: {c['claim']}\n원문 구절: {c['quote']}\n\n검색 결과:\n{listing}", max_tokens=1200)
            v = j.get("verdict")
            status = {"supported": "confirmed", "contradicted": "mismatch"}.get(v, "unverified")
            si = j.get("source_index")
            srcs = [results[si]] if isinstance(si, int) and 0 <= si < len(results) else results[:2]
            out.update(method="liner-search", status=status, reason=j.get("reason") or "", sources=srcs,
                       fix=j.get("fix") if status == "mismatch" else None)
        except Exception as e:  # noqa: BLE001 — 한 주장의 실패가 전체를 멈추지 않게
            out.update(method="error", status="unverified", reason=f"확인 중 오류: {e}", sources=[], fix=None)
        return out

    emit({"stage": "verify", "msg": "1차 통계와 Liner 검색으로 대조하는 중"})
    checked: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for res in pool.map(check, claims):
            checked.append(res)
            emit({"stage": "claim", "claim": res})

    # 3) 규칙 검토
    emit({"stage": "rules", "msg": f"검토 규칙 {len(pack['rules'])}개를 적용하는 중"})
    rules_txt = "\n".join(f"- {r['id']} {r['name']}: {r['check']}" for r in pack["rules"])
    rv = liner.chat_json(RULES_SYS.format(rules=rules_txt, max_findings=8), f"원고:\n\n{text}", max_tokens=4000,
                         effort="medium")
    by_id = {r["id"]: r for r in pack["rules"]}
    findings = []
    for k, f in enumerate(rv.get("findings") or []):
        if not isinstance(f, dict) or f.get("rule_id") not in by_id:
            continue
        r = by_id[f["rule_id"]]
        findings.append({"id": f"F{k + 1:02d}", "rule_id": r["id"], "rule_name": r["name"], "origin": r.get("origin", ""),
                         "quote": f.get("quote") or "", "why": f.get("why") or "", "fix": f.get("fix") or "",
                         "span": _span(text, f.get("quote") or "")})

    summary = {s: sum(1 for c in checked if c["status"] == s) for s in ("confirmed", "mismatch", "unverified")}
    summary["rule_findings"] = len(findings)
    summary["primary_data"] = sum(1 for c in checked if c.get("method") == "primary-data")
    result = {"text": text, "pack": {"id": pack["id"], "name": pack["name"], "rules": len(pack["rules"])},
              "claims": checked, "findings": findings, "overall": rv.get("overall") or "", "summary": summary,
              "as_of": as_of, "usage": liner.usage.as_dict(), "generated": dt.datetime.now().isoformat(timespec="seconds")}
    emit({"stage": "done", "result": result})
    return result
