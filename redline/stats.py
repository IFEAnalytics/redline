"""1차 통계 대조 — 원고의 수치를 검색 결과가 아니라 통계 원자료로 다시 계산한다.

FRED 공개 CSV 주소는 키가 필요 없다. 그래서 심사·재현 환경에서 Liner 키 하나로 전부 돈다.
"""
from __future__ import annotations

import calendar
import datetime as dt
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
OECD_CSV = "https://sdmx.oecd.org/public/rest/data/{flow}/{key}?startPeriod=2015-01"


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
    """(날짜, 값) 오름차순. 결측은 뺀다. 목록의 source 에 따라 FRED 또는 OECD 에서 받는다."""
    meta = catalog()["series"].get(series_id, {})
    last = None
    for attempt in range(3):
        try:
            rows = _fetch_oecd(meta) if meta.get("source") == "oecd" else _fetch_fred(series_id)
            if rows:
                return tuple(sorted(rows))
            last = RuntimeError("빈 응답")
        except Exception as e:  # noqa: BLE001 — 통계 서버 오류는 '대조 불가'로 넘긴다
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"통계 {series_id} 조회 실패: {last}")


def _fetch_fred(series_id: str) -> list[tuple[str, float]]:
    r = requests.get(FRED_CSV.format(id=series_id, start="2015-01-01"), timeout=40)
    r.raise_for_status()
    return [(row[0], float(row[1])) for row in list(csv.reader(io.StringIO(r.text)))[1:]
            if len(row) >= 2 and row[1] not in (".", "")]


def _fetch_oecd(meta: dict) -> list[tuple[str, float]]:
    r = requests.get(OECD_CSV.format(flow=meta["flow"], key=meta["key"]), timeout=60,
                     headers={"Accept": "application/vnd.sdmx.data+csv"})
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        t, v = row.get("TIME_PERIOD", ""), row.get("OBS_VALUE", "")
        if not t or v in ("", "NaN"):
            continue
        m = re.fullmatch(r"(\d{4})-Q([1-4])", t)
        date = f"{m.group(1)}-{(int(m.group(2)) - 1) * 3 + 1:02d}-01" if m else f"{t}-01"
        out.append((date, float(v)))
    return out


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


def observe(series_id: str, transform: str, period: str, *, adjustment=None, as_of=None) -> Observed:
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
    # CPI headlines conventionally use an unadjusted year-on-year series. An
    # explicit seasonal-adjustment basis in the manuscript takes precedence.
    if "unadjusted_id" in meta:
        if adjustment not in (None, "sa", "nsa"):
            raise ValueError("계절조정 기준을 확인할 수 없습니다")
        if adjustment == "nsa" or (adjustment is None and transform == "yoy"):
            series_id = meta["unadjusted_id"]
    rows = fetch(series_id)
    freq = meta["freq"]
    if freq == "D":
        target = dt.date.fromisoformat(date)
        if grain == "M":
            target = target.replace(day=calendar.monthrange(target.year, target.month)[1])
        elif grain != "D":
            raise ValueError("일별 통계에는 날짜 또는 월말을 지정해 주세요")
        cutoff = dt.date.fromisoformat(as_of) if as_of else dt.date.today()
        if target > cutoff:
            raise ValueError("원고 기준일에 해당 기간이 끝나지 않았습니다")
        i = _index_on_or_before(rows, target.isoformat())
        if i is None:
            raise ValueError("해당 기간 관측 없음")
        observed_date = dt.date.fromisoformat(rows[i][0])
        if grain == "D" and observed_date != target:
            raise ValueError("지정한 날짜의 관측이 없습니다")
        if grain == "M" and (observed_date.month != target.month or (target - observed_date).days > 4):
            raise ValueError("해당 월말의 최근 관측이 없습니다")
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
    url = ("https://data-explorer.oecd.org/" if meta.get("source") == "oecd"
           else f"https://fred.stlouisfed.org/series/{series_id}")
    return Observed(series_id, transform, period, d, round(val, 4), unit, detail, url)


def display_value(observed: Observed) -> str:
    if observed.transform in ("yoy", "mom", "saar"):
        return f"{observed.value:.1f}"
    return f"{observed.value:.4f}".rstrip("0").rstrip(".")


def compare(claimed: float, observed: float, transform: str, loose: bool = False) -> tuple[str, float]:
    """(판정, 차이). 반올림 한 자리 차이는 일치로 본다.

    loose: 기준연도를 바꿔 다시 낸 지수(OECD 수록분)로 계산한 증가율은 공식 발표치와 0.1%p 까지 어긋날 수 있어 폭을 넓힌다.
    """
    diff = claimed - observed
    tol = (0.11 if loose else 0.06) if transform in ("yoy", "mom", "saar") else 0.005001
    if loose and transform == "level":
        tol = max(tol, 0.06)
    return ("match" if abs(diff) <= tol else "mismatch"), round(diff, 4)
