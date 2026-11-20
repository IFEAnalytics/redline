"""1차 통계 대조 — 원고의 수치를 검색 결과가 아니라 통계 원자료로 다시 계산한다.

FRED 공개 CSV 주소는 키가 필요 없다. 그래서 심사·재현 환경에서 Liner 키 하나로 전부 돈다.
"""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import requests
import yaml

CATALOG = Path(__file__).resolve().parent.parent / "rulepacks" / "series.yaml"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"


@lru_cache(maxsize=1)
def catalog() -> dict:
    d = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    return {"source": d["source"], "series": {s["id"]: s for s in d["series"]}}


def catalog_prompt() -> str:
    """모델에게 보여 줄 시리즈 목록 한 덩어리."""
    lines = []
    for s in catalog()["series"].values():
        lines.append(f'- {s["id"]}: {s["name"]} / {s["en"]} (주기 {s["freq"]}, 변환 {",".join(s["transforms"])})')
    return "\n".join(lines)


@lru_cache(maxsize=64)
def fetch(series_id: str) -> tuple[tuple[str, float], ...]:
    """(날짜, 값) 오름차순. 결측('.')은 뺀다."""
    last = None
    for attempt in range(3):
        try:
            r = requests.get(FRED_CSV.format(id=series_id, start="2015-01-01"), timeout=40)
            r.raise_for_status()
            rows = []
            for row in list(csv.reader(io.StringIO(r.text)))[1:]:
                if len(row) >= 2 and row[1] not in (".", ""):
                    rows.append((row[0], float(row[1])))
            if rows:
                return tuple(rows)
            last = RuntimeError("빈 응답")
        except Exception as e:  # noqa: BLE001 — 통계 서버 오류는 '대조 불가'로 넘긴다
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"FRED {series_id} 조회 실패: {last}")


def parse_period(p: str) -> tuple[str, str] | None:
    """'2026-09' → ('M','2026-09-01'), '2026-Q3' → ('Q','2026-07-01'), '2026-09-17' → ('D', 그대로)."""
    p = (p or "").strip().upper().replace(" ", "")
    m = re.fullmatch(r"(\d{4})-?Q([1-4])", p)
    if m:
        return "Q", f"{m.group(1)}-{(int(m.group(2)) - 1) * 3 + 1:02d}-01"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m:
        return "M", f"{m.group(1)}-{int(m.group(2)):02d}-01"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", p)
    if m:
        return "D", f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


@dataclass
class Observed:
    series_id: str
    transform: str
    period: str
    date: str
    value: float
    unit: str
    detail: str
    url: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _index_on_or_before(rows, date: str) -> int | None:
    idx = None
    for i, (d, _) in enumerate(rows):
        if d <= date:
            idx = i
        else:
            break
    return idx


def observe(series_id: str, transform: str, period: str) -> Observed:
    """시리즈·변환·기간으로 통계 원자료의 값을 계산한다. 못 구하면 ValueError."""
    cat = catalog()["series"]
    if series_id not in cat:
        raise ValueError(f"목록에 없는 시리즈: {series_id}")
    meta = cat[series_id]
    if transform not in meta["transforms"]:
        raise ValueError(f"{series_id} 는 {transform} 변환을 지원하지 않습니다")
    pp = parse_period(period)
    if not pp:
        raise ValueError(f"기간을 읽을 수 없습니다: {period}")
    grain, date = pp
    rows = fetch(series_id)
    freq = meta["freq"]
    if freq == "D":
        i = _index_on_or_before(rows, date if grain == "D" else date[:8] + "31")
        if grain != "D":  # 월을 준 일별 시리즈: 그 달의 마지막 관측
            i = _index_on_or_before(rows, _month_end(date))
        if i is None:
            raise ValueError("해당 기간 관측 없음")
    else:
        if grain == "D":
            date = date[:8] + "01"
        if freq == "Q":
            y, mth = int(date[:4]), int(date[5:7])
            date = f"{y}-{((mth - 1) // 3) * 3 + 1:02d}-01"
        i = next((k for k, (d, _) in enumerate(rows) if d == date), None)
        if i is None:
            raise ValueError(f"{period} 관측이 아직 없습니다(최근 {rows[-1][0]})")
    d, v = rows[i]
    unit = meta.get("unit", "")
    by_date = dict(rows)

    def prior(months: int) -> tuple[str, float]:
        """달력으로 months 개월 전 관측. 결측 월이 있어도 줄 번호가 아니라 날짜로 찾는다."""
        if freq == "D":  # 일별 시리즈는 직전 관측일
            if i - 1 < 0:
                raise ValueError("직전 값 없음")
            return rows[i - 1]
        y, m = int(d[:4]), int(d[5:7]) - months
        while m <= 0:
            y, m = y - 1, m + 12
        key = f"{y}-{m:02d}-01"
        if key not in by_date:
            raise ValueError(f"비교 시점({key}) 관측이 통계에 없습니다(결측)")
        return key, by_date[key]

    step = 3 if freq == "Q" else 1
    if transform == "level":
        val, detail = v, f"{d} 값 {v:g}"
    elif transform == "yoy":
        d0, v0 = prior(12)
        val, unit, detail = (v / v0 - 1) * 100, "%", f"{d} {v:g} ÷ {d0} {v0:g} − 1"
    elif transform in ("mom", "chg", "saar"):
        d0, v0 = prior(step)
        if transform == "mom":
            val, unit, detail = (v / v0 - 1) * 100, "%", f"{d} {v:g} ÷ {d0} {v0:g} − 1"
        elif transform == "chg":
            val, detail = v - v0, f"{d} {v:g} − {d0} {v0:g}"
            unit = "%p" if unit == "%" else unit
        else:
            val, unit, detail = ((v / v0) ** 4 - 1) * 100, "%", f"({d} {v:g} ÷ {d0} {v0:g})^4 − 1"
    else:
        raise ValueError(transform)
    return Observed(series_id, transform, period, d, round(val, 4), unit, detail,
                    f"https://fred.stlouisfed.org/series/{series_id}")


def _month_end(date: str) -> str:
    return date[:8] + "31"


def compare(claimed: float, observed: float, transform: str) -> tuple[str, float]:
    """(판정, 차이). 반올림 한 자리 차이는 일치로 본다."""
    diff = claimed - observed
    tol = 0.06 if transform in ("yoy", "mom", "saar") else max(0.011, abs(observed) * 0.002)
    if transform == "chg" and abs(observed) > 20:  # 취업자 증감(천 명) 같은 큰 단위
        tol = max(1.5, abs(observed) * 0.02)
    return ("match" if abs(diff) <= tol else "mismatch"), round(diff, 4)
