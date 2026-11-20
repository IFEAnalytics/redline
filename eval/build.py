"""오류 심기 실험용 원고 만들기.

통계 원자료(FRED)에서 실제 값을 받아 '참인 문장'을 만들고, 그 사이에 유형별 오류를 심는다.
어느 문장이 참이고 어느 문장에 무슨 오류를 심었는지는 정답표로 함께 저장한다(eval/cases/*.json).

    python eval/build.py            # eval/cases 를 다시 만든다 (네트워크: FRED)
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from redline import stats  # noqa: E402

OUT = Path(__file__).resolve().parent / "cases"
MONTHS = ["2026-06", "2026-07", "2026-08"]  # 원고가 다루는 달 (모두 통계가 확정 발표된 달)


def ko_month(p: str) -> str:
    return f"{int(p[5:7])}월"


def people(thousands: float) -> str:
    n = abs(round(thousands))
    man, chun = divmod(n, 10)
    return (f"{man}만 " if man else "") + (f"{chun}천 " if chun else "") + "명"


# ── 참인 문장 틀: (시리즈, 변환, 문장 만드는 함수) ──
def s_yoy(name):
    return lambda p, v: f"{ko_month(p)} {name}는 전년 동월 대비 {abs(v):.1f}% {'내렸다' if round(v, 1) < 0 else '올랐다'}."


def s_mom(name):
    return lambda p, v: f"{ko_month(p)} {name}는 전월 대비 {abs(v):.1f}% {'줄었다' if round(v, 1) < 0 else '늘었다'}."


TOPICS = {
    "prices": {
        "title": "{m} 미국 물가 점검",
        "lead": "{y}년 {m} 미국 물가 지표를 정리한다.",
        "facts": [("CPIAUCSL", "yoy", s_yoy("소비자물가지수(CPI)")), ("CPILFESL", "yoy", s_yoy("근원 CPI")),
                  ("PCEPILFE", "yoy", s_yoy("근원 PCE 물가지수")), ("PCEPI", "yoy", s_yoy("PCE 물가지수"))],
    },
    "jobs": {
        "title": "{m} 미국 고용 점검",
        "lead": "{y}년 {m} 미국 고용 지표를 정리한다.",
        "facts": [("UNRATE", "level", lambda p, v: f"{ko_month(p)} 실업률은 {v:.1f}%였다."),
                  ("PAYEMS", "chg", lambda p, v: f"{ko_month(p)} 비농업 취업자는 전월보다 {people(v)} {'늘었다' if v >= 0 else '줄었다'}."),
                  ("CPIAUCSL", "yoy", s_yoy("소비자물가지수(CPI)")), ("INDPRO", "yoy", s_yoy("산업생산지수"))],
    },
    "rates": {
        "title": "{m} 미국 금리 점검",
        "lead": "{y}년 {m} 미국 금리와 물가를 정리한다.",
        "facts": [("DGS10", "level", lambda p, v: f"미국 국채 10년물 금리는 {ko_month(p)} 말 {v:.2f}%였다."),
                  ("DGS2", "level", lambda p, v: f"미국 국채 2년물 금리는 {ko_month(p)} 말 {v:.2f}%였다."),
                  ("FEDFUNDS", "level", lambda p, v: f"{ko_month(p)} 연방기금 실효금리는 월평균 {v:.2f}%였다."),
                  ("PCEPILFE", "yoy", s_yoy("근원 PCE 물가지수"))],
    },
    "activity": {
        "title": "{m} 미국 실물 경기 점검",
        "lead": "{y}년 {m} 미국 실물 지표를 정리한다.",
        "facts": [("INDPRO", "yoy", s_yoy("산업생산지수")), ("RSAFS", "mom", s_mom("소매판매")),
                  ("UNRATE", "level", lambda p, v: f"{ko_month(p)} 실업률은 {v:.1f}%였다."),
                  ("CPILFESL", "yoy", s_yoy("근원 CPI"))],
    },
}

# ── 규칙 위반 문장 (규칙 id, 문장). 사실 대조로는 못 잡고 선임의 규칙으로 잡아야 하는 것들 ──
RULE_SEEDS = [
    ("R10", "같은 달 주가가 올랐으니, 이는 물가 둔화가 주가를 끌어올렸다는 증거다."),
    ("R10", "금리가 내린 주에 소비가 늘었으므로 금리 인하가 소비를 살린 것이 분명하다."),
    ("R03", "모든 업종에서 예외 없이 고용이 늘었다."),
    ("R03", "미국 기업들은 일제히 가격을 올렸다."),
    ("R11", "따라서 지금 장기 국채를 사 두어야 한다."),
    ("R11", "이 구간에서는 주식 비중을 늘리면 반드시 수익이 난다."),
    ("R06", "생산자물가의 trade services는 무역 서비스의 가격을 뜻한다."),
    ("R05", "평균 관세율이 내렸으므로 관세 수입 총액도 줄어든 셈이다."),
    ("R04", "두 회사가 양해각서를 맺었으므로 공급은 이미 시작됐다."),
    ("R12", "한 조사에 따르면 현재 기업의 12%가 AI를 쓰고 있다(2023년 조사)."),
    ("R01", "시장에서는 내년 성장률을 1.4%로 보고 있다."),
    ("R09", "연준은 다음 회의에서 금리를 내릴 것이다."),
]


# ── 검색으로 대조해야 하는 제도·정의 (참인 문장, 틀리게 바꾼 문장). 통계 목록에 없어 Liner 검색 경로를 탄다 ──
SEARCH_PAIRS = [
    ("미국 개인소비지출(PCE) 물가지수는 상무부 경제분석국(BEA)이 발표한다.", "미국 개인소비지출(PCE) 물가지수는 노동부 노동통계국(BLS)이 발표한다."),
    ("연준의 물가 목표는 PCE 물가 상승률 기준 연 2%다.", "연준의 물가 목표는 PCE 물가 상승률 기준 연 3%다."),
    ("연방공개시장위원회(FOMC)는 한 해에 정례회의를 여덟 번 연다.", "연방공개시장위원회(FOMC)는 한 해에 정례회의를 열두 번 연다."),
    ("미국 연방정부의 회계연도는 10월 1일에 시작한다.", "미국 연방정부의 회계연도는 1월 1일에 시작한다."),
    ("미국 소비자물가지수(CPI)는 노동통계국(BLS)이 매달 발표한다.", "미국 소비자물가지수(CPI)는 연방준비제도가 매달 발표한다."),
    ("미국 실질 GDP 속보치는 분기가 끝난 뒤 약 한 달 뒤에 나온다.", "미국 실질 GDP 속보치는 분기가 끝난 뒤 약 여섯 달 뒤에 나온다."),
    ("근원 물가지수는 식품과 에너지를 뺀 지수다.", "근원 물가지수는 주거비와 의료비를 뺀 지수다."),
    ("미국 고용보고서의 실업률은 가계조사에서 나온다.", "미국 고용보고서의 실업률은 실업수당 청구 건수에서 계산한다."),
]


def perturb(transform: str, v: float, rng: random.Random) -> float:
    """수치 오기: 반올림 차이로는 설명되지 않는 크기로 틀리게."""
    if transform == "chg" and abs(v) > 20:
        return v + rng.choice([-1, 1]) * max(60, abs(v) * 0.8)
    step = {"yoy": 0.6, "mom": 0.5, "saar": 0.9}.get(transform, 0.4)
    return v + rng.choice([-1, 1]) * step


def build() -> list[dict]:
    rng = random.Random(20261120)
    seeds = RULE_SEEDS[:]
    rng.shuffle(seeds)
    cases, k = [], 0
    for month in MONTHS:
        for topic, spec in TOPICS.items():
            k += 1
            facts = spec["facts"][:]
            rng.shuffle(facts)
            parts = []  # (kind, sentence, meta)
            # 참인 문장 2개 (대조군)
            for sid, tr, fn in facts[:2]:
                o = stats.observe(sid, tr, month)
                parts.append(("control", fn(month, o.value), {"series": sid, "transform": tr, "true": o.value}))
            # 수치 오기 1개
            sid, tr, fn = facts[2]
            o = stats.observe(sid, tr, month)
            wrong = perturb(tr, o.value, rng)
            parts.append(("fact_value", fn(month, wrong), {"series": sid, "transform": tr, "true": o.value, "written": round(wrong, 2)}))
            # 기준 혼동 1개: 전월 대비 값을 '전년 동월 대비'라고 쓰거나(지수), 한 달 전 값을 이번 달 값으로 쓴다(수준)
            sid, tr, fn = facts[3]
            o = stats.observe(sid, tr, month)
            if tr == "yoy":
                m = stats.observe(sid, "mom", month)
                parts.append(("fact_basis", fn(month, m.value), {"series": sid, "transform": tr, "true": o.value, "written": round(m.value, 2),
                                                                 "note": "전월 대비 값을 전년 동월 대비로 적음"}))
            else:
                wrong = perturb(tr, o.value, rng)
                parts.append(("fact_value", fn(month, wrong), {"series": sid, "transform": tr, "true": o.value, "written": round(wrong, 2)}))
            # 규칙 위반 2개
            for rid, sent in (seeds[(2 * k - 2) % len(seeds)], seeds[(2 * k - 1) % len(seeds)]):
                parts.append(("rule", sent, {"rule_id": rid}))
            body = parts[:]
            rng.shuffle(body)
            title = spec["title"].format(m=ko_month(month))
            lead = (spec["lead"].format(y=month[:4], m=ko_month(month))
                    + " 아래 수치는 모두 미국 노동통계국(BLS)·경제분석국(BEA)·연준의 공식 발표치이며 FRED 수록분을 썼다.")
            text, labels = f"{title}\n\n{lead}", []
            for i, (kind, sent, meta) in enumerate(body):
                text += "\n\n" if i % 3 == 0 else " "
                start = len(text)
                text += sent
                labels.append({"kind": kind, "span": [start, len(text)], "sentence": sent, **meta})
            cases.append({"id": f"{month}-{topic}", "as_of": "2026-11-20", "text": text, "labels": labels})
    # 제도·정의 원고 4편: 편마다 틀린 문장 2개 + 참인 문장 2개
    pairs = SEARCH_PAIRS[:]
    rng.shuffle(pairs)
    for n in range(4):
        wrong = pairs[2 * n: 2 * n + 2]
        true = pairs[(2 * n + 4) % 8: (2 * n + 4) % 8 + 2]
        body = [("fact_search", w, {"true_sentence": t}) for t, w in wrong] + [("control_search", t, {}) for t, _ in true]
        rng.shuffle(body)
        text, labels = f"미국 통계·제도 메모 {n + 1}\n\n지표를 읽기 전에 확인해 둘 제도와 정의를 적는다.\n\n", []
        for i, (kind, sent, meta) in enumerate(body):
            text += " " if i else ""
            start = len(text)
            text += sent
            labels.append({"kind": kind, "span": [start, len(text)], "sentence": sent, **meta})
        cases.append({"id": f"facts-{n + 1}", "as_of": "2026-11-20", "text": text, "labels": labels})
    return cases


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    cs = build()
    for c in cs:
        (OUT / f"{c['id']}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    print(len(cs), "편", dict(Counter(l["kind"] for c in cs for l in c["labels"])))
    print(cs[0]["text"])
