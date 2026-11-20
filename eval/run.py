"""오류 심기 실험 실행·채점.

    python eval/run.py              # eval/cases 전부 → eval/results.json, eval/RESULTS.md
    python eval/run.py --limit 2    # 일부만

채점 규칙 (문장 단위, 표시 구간이 정답 문장과 겹치면 그 문장을 짚은 것으로 본다):
- 사실 오류(수치 오기·기준 혼동): '불일치' 판정이 그 문장에 붙으면 적발.
- 규칙 위반: 그 문장에 규칙 위반 표시가 붙으면 적발, 규칙 번호까지 같으면 '정확 적발'.
  (규칙 위반 문장이 사실 대조에서 '불일치'로 잡힌 경우도 적발로 센다 — 독자에게는 빨간 줄이 그어진 것이므로.)
- 참인 문장(대조군): '불일치'가 붙으면 오경보, '확인'이 붙으면 정상 확인.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from redline.liner import Liner  # noqa: E402
from redline.pipeline import _span, review  # noqa: E402

BASELINE_SYS = """당신은 보고서를 검토하는 선임이다. 아래 원고를 읽고 문제를 짚는다. 도구는 없다 — 아는 것만으로 판단한다.
JSON 객체 하나로만 답한다:
{"wrong_facts":[{"quote":"사실이 틀린 구절을 원고 그대로","why":"이유"}],"issues":[{"quote":"사실 오류는 아니지만 고쳐야 할 구절을 원고 그대로","why":"이유"}]}
quote 는 반드시 원고에 있는 그대로 복사한다. 원고 기준일: {as_of}"""


def baseline(case: dict, liner: Liner) -> dict:
    """비교 기준: 같은 모델에 원고만 주고 묻는다(통계 대조·검색·규칙 묶음 없음). review() 결과와 같은 모양으로 돌려준다."""
    j = liner.chat_json(BASELINE_SYS.replace("{as_of}", case["as_of"]), f"원고:\n\n{case['text']}", max_tokens=4000, effort="medium")
    claims = [{"status": "mismatch", "method": "model-only", "span": _span(case["text"], w.get("quote") or "")}
              for w in j.get("wrong_facts") or [] if isinstance(w, dict)]
    finds = [{"rule_id": "-", "span": _span(case["text"], w.get("quote") or "")}
             for w in j.get("issues") or [] if isinstance(w, dict)]
    return {"claims": claims, "findings": finds, "usage": liner.usage.as_dict()}

KIND_KO = {"fact_search": "제도·정의 오류", "control_search": "참인 제도·정의 문장", "fact_value": "수치 오기", "fact_basis": "기준 혼동(전월↔전년)", "rule": "규칙 위반", "control": "참인 문장(대조군)"}


def overlap(a, b) -> bool:
    return bool(a and b and a[0] < b[1] and b[0] < a[1])


def score_case(case: dict, res: dict) -> list[dict]:
    rows = []
    for lab in case["labels"]:
        claims = [c for c in res["claims"] if overlap(c.get("span"), lab["span"])]
        finds = [f for f in res["findings"] if overlap(f.get("span"), lab["span"])]
        mism = [c for c in claims if c["status"] == "mismatch"]
        row = {"case": case["id"], "kind": lab["kind"], "sentence": lab["sentence"],
               "mismatch": bool(mism), "confirmed": any(c["status"] == "confirmed" for c in claims),
               "primary": any(c.get("method") == "primary-data" for c in mism or claims),
               "rules_hit": [f["rule_id"] for f in finds]}
        if lab["kind"] in ("fact_value", "fact_basis", "fact_search"):
            row["caught"] = row["mismatch"]
        elif lab["kind"] == "rule":
            row["caught"] = bool(finds) or row["mismatch"]
            row["exact"] = lab["rule_id"] in row["rules_hit"]
            row["expected_rule"] = lab["rule_id"]
        else:
            row["false_alarm"] = row["mismatch"]
            row["marked"] = row["mismatch"] or bool(finds)  # 참인 문장에 어떤 표시든 붙었는가
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    env = HERE.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((HERE / "cases").glob("*.json"))]
    if a.limit:
        cases = cases[:a.limit]
    t0 = time.time()
    usage = {"model_calls": 0, "search_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def run(case):
        liner = Liner()
        try:
            res = review(case["text"], "research", liner=liner, as_of=case["as_of"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! {case['id']} 실패: {e}", file=sys.stderr)
            return case, None, None
        try:
            base = baseline(case, Liner())
        except Exception as e:  # noqa: BLE001
            print(f"  ! {case['id']} 비교 기준 실패: {e}", file=sys.stderr)
            return case, res, None
        print(f"  · {case['id']} 주장 {len(res['claims'])} 규칙 {len(res['findings'])}", file=sys.stderr)
        return case, res, base

    rows, brows, raw, failed = [], [], {}, []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for case, res, base in pool.map(run, cases):
            if res is None or base is None:
                failed.append(case["id"])
                continue
            raw[case["id"]] = res
            rows += score_case(case, res)
            brows += score_case(case, base)
            for k in usage:
                usage[k] += res["usage"][k]

    if failed:
        print("양쪽 검토를 완료하지 못한 원고: " + ", ".join(failed), file=sys.stderr)
        return 1

    def rate(kind, key, src=None):
        sel = [r for r in (rows if src is None else src) if r["kind"] == kind]
        return sum(1 for r in sel if r.get(key)), len(sel)

    summary = {"cases": len(cases) - len(failed), "failed": failed, "seconds": round(time.time() - t0),
               "usage": usage, "model": sorted({m for r in raw.values() for m in r["usage"]["models"]}),
               "fact_value": rate("fact_value", "caught"), "fact_basis": rate("fact_basis", "caught"),
               "rule": rate("rule", "caught"), "rule_exact": rate("rule", "exact"),
               "control_false_alarm": rate("control", "false_alarm"), "control_confirmed": rate("control", "confirmed"),
               "fact_search": rate("fact_search", "caught"), "search_false_alarm": rate("control_search", "false_alarm"),
               "search_confirmed": rate("control_search", "confirmed"),
               "control_marked": rate("control", "marked"),
               "baseline": {"fact_value": rate("fact_value", "caught", brows), "fact_basis": rate("fact_basis", "caught", brows),
                            "rule": rate("rule", "caught", brows), "control_false_alarm": rate("control", "false_alarm", brows),
                            "fact_search": rate("fact_search", "caught", brows),
                            "search_false_alarm": rate("control_search", "false_alarm", brows),
                            "control_marked": rate("control", "marked", brows)}}
    if revision := os.environ.get("REDLINE_EVAL_REVISION"):
        summary["revision"] = revision
    (HERE / "results.json").write_text(json.dumps({"summary": summary, "rows": rows, "baseline_rows": brows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "RESULTS.md").write_text(render(summary, rows), encoding="utf-8")
    print(render(summary, rows).split("## 놓친")[0])
    return 0


def pct(t) -> str:
    n, d = t
    return f"{n}/{d} ({(100 * n / d if d else 0):.0f}%)"


def render(s: dict, rows: list[dict]) -> str:
    fact = (s["fact_value"][0] + s["fact_basis"][0], s["fact_value"][1] + s["fact_basis"][1])
    b = s["baseline"]
    bfact = (b["fact_value"][0] + b["fact_basis"][0], b["fact_value"][1] + b["fact_basis"][1])
    out = ["# 오류 심기 실험 결과", "",
           f"원고 {s['cases']}편, 실행 {s['seconds']}초, 모델 {', '.join(s['model'])}. "
           f"Liner 모델 {s['usage']['model_calls']}회 · 검색 {s['usage']['search_calls']}회.", "",
           "| 심은 것 | Redline | 원고만 받은 Liner |", "|---|---|---|",
           f"| 수치 오류 전체 | {pct(fact)} | {pct(bfact)} |",
           f"| └ 수치 오기 | {pct(s['fact_value'])} | {pct(b['fact_value'])} |",
           f"| └ 기준 혼동(전월 대비 값을 전년 대비로) | {pct(s['fact_basis'])} | {pct(b['fact_basis'])} |",
           f"| 규칙 위반 문장 | {pct(s['rule'])} | {pct(b['rule'])} |",
           f"| └ 규칙 번호까지 일치 | {pct(s['rule_exact'])} | — |",
           f"| 제도·정의 오류 (Liner 검색으로 대조) | {pct(s['fact_search'])} | {pct(b['fact_search'])} |",
           f"| 참인 통계 문장을 틀렸다고 잘못 짚음(오경보) | {pct(s['control_false_alarm'])} | {pct(b['control_false_alarm'])} |",
           f"| 맞는 통계 문장에 사실·문장 검토 지적을 붙임 | {pct(s['control_marked'])} | {pct(b['control_marked'])} |",
           f"| 참인 제도·정의 문장을 틀렸다고 잘못 짚음 | {pct(s['search_false_alarm'])} | {pct(b['search_false_alarm'])} |",
           f"| 참인 제도·정의 문장을 Liner 검색으로 '확인' | {pct(s['search_confirmed'])} | — |",
           f"| 참인 문장을 통계 원자료로 '확인' | {pct(s['control_confirmed'])} | — |", "",
           "범용 AI는 같은 Liner 모델에 원고만 주고 '틀린 곳과 고칠 곳을 짚으라'고 물은 결과다(통계 대조·검색·규칙 묶음 없음).", "",
           "## 읽는 법과 한계", "",
           "- 미국 CPI 전년비 정답은 BLS 발표문에서 별도로 기록했다. 나머지 통계 문장은 FRED에서 계산한 값으로 생성했다. 정답표는 `eval/cases/*.json`.",
           "- 심은 수치 오류는 Redline의 통계 목록(미국 지표 16종) 안의 지표다. 목록 밖 사실은 Liner 검색 판정에 의존하며, 제도·정의 8쌍으로만 쟀다.",
           "- 심은 규칙 위반 문장은 눈에 띄게 쓴 것이라 범용 AI도 잘 잡는다. Redline이 보태는 것은 어느 규칙을 왜 어겼는지와 고칠 문장이다.",
           "- 오류를 심은 사람과 도구를 만든 사람이 같다. 남이 쓴 실제 원고에서의 성능은 이 표보다 낮을 수 있다.",
           "- 모델 응답이 실행마다 조금씩 달라 숫자는 재실행하면 몇 건씩 움직인다.", "",
           "## 놓친 것과 잘못 짚은 것", ""]
    if s.get("revision"):
        out[2:2] = [f"검증한 제품 커밋 `{s['revision']}`", ""]
    miss = [r for r in rows if not r["kind"].startswith("control") and not r["caught"]]
    fa = [r for r in rows if r["kind"].startswith("control") and r.get("false_alarm")]
    for r in miss:
        out.append(f"- 놓침 [{KIND_KO[r['kind']]}{' ' + r['expected_rule'] if r.get('expected_rule') else ''}] {r['case']}: {r['sentence']}")
    for r in fa:
        out.append(f"- 오경보 {r['case']}: {r['sentence']}")
    if not miss and not fa:
        out.append("- 없음")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
