"""검토 결과를 한 장짜리 마크다운 보고서로."""
from __future__ import annotations

LABEL = {"confirmed": "확인", "mismatch": "불일치", "unverified": "확인 불가"}


def to_markdown(res: dict) -> str:
    s = res["summary"]
    out = [f"# Redline 검토 보고서", "",
           f"- 규칙 묶음: {res['pack']['name']} (규칙 {res['pack']['rules']}개) · 원고 기준일 {res.get('as_of', '')} · 생성 {res['generated']}",
           f"- 주장 {len(res['claims'])}건: 확인 {s['confirmed']} · 불일치 {s['mismatch']} · 확인 불가 {s['unverified']} "
           f"(통계 원자료 대조 {s['primary_data']}건) · 규칙 위반 {s['rule_findings']}곳",
           f"- 총평: {res.get('overall', '')}", "", "## 고칠 곳", ""]
    n = 0
    for c in res["claims"]:
        if c["status"] != "mismatch":
            continue
        n += 1
        out += [f"{n}. **{c['quote']}**", f"   - {c['reason']}"]
        if c.get("fix"):
            out.append(f"   - 수정 제안: {c['fix']}")
        for src in c.get("sources") or []:
            out.append(f"   - 근거: [{src['title']}]({src['url']})")
    for f in res["findings"]:
        n += 1
        out += [f"{n}. **{f['quote']}** — {f['rule_id']} {f['rule_name']}", f"   - {f['why']}", f"   - 수정 제안: {f['fix']}"]
    if n == 0:
        out.append("고칠 곳을 찾지 못했다.")
    out += ["", "## 주장 원장", "", "| # | 판정 | 방법 | 원고 구절 | 근거 |", "|---|---|---|---|---|"]
    for c in res["claims"]:
        how = "통계 원자료" if c.get("method") == "primary-data" else ("Liner 검색" if c.get("method") == "liner-search" else "대조 불가")
        src = (c.get("sources") or [{}])[0]
        link = f"[{src.get('host', '')}]({src.get('url', '')})" if src.get("url") else "—"
        q = c["quote"].replace("|", "/").replace("\n", " ")
        out.append(f"| {c['id']} | {LABEL[c['status']]} | {how} | {q} | {link} |")
    u = res["usage"]
    out += ["", f"Liner 호출: 모델 {u['model_calls']}회({', '.join(u['models'])}) · 검색 {u['search_calls']}회. "
            "수치 대조 자료: FRED · OECD.",
            "", "> 이 보고서는 초안 검토를 돕는 도구의 출력이다. '확인'은 근거 자료와 일치한다는 뜻이며 최종 판단은 작성자가 한다. 투자 자문이 아니다."]
    return "\n".join(out) + "\n"
