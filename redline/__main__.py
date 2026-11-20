"""명령줄: python -m redline review <원고.md> [--pack research] [--as-of 2026-11-20] [--json out.json]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_env() -> None:
    env = Path(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


MARK = {"confirmed": "✓ 확인", "mismatch": "✗ 불일치", "unverified": "? 확인 불가"}


def main(argv: list[str] | None = None) -> int:
    _load_env()
    ap = argparse.ArgumentParser(prog="redline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review", help="원고 하나를 검토한다")
    r.add_argument("file")
    r.add_argument("--pack", default="research")
    r.add_argument("--as-of", default=None, help="원고 기준일 YYYY-MM-DD (기본: 오늘)")
    r.add_argument("--json", default=None, help="결과를 JSON 파일로 저장")
    sub.add_parser("packs", help="규칙 묶음 목록")
    a = ap.parse_args(argv)

    from .pipeline import list_packs, review
    if a.cmd == "packs":
        for p in list_packs():
            print(f'{p["id"]:10} {p["name"]} — 규칙 {p["rules"]}개')
        return 0

    text = Path(a.file).read_text(encoding="utf-8")
    res = review(text, a.pack, as_of=a.as_of,
                 on_event=lambda e: print(f"… {e['msg']}", file=sys.stderr) if e.get("msg") else None)
    print(f"\n■ 주장 대조 ({len(res['claims'])}건)")
    for c in res["claims"]:
        how = "1차 통계" if c.get("method") == "primary-data" else "Liner 검색"
        print(f"  {MARK[c['status']]:8} [{how}] {c['quote'][:60]}")
        print(f"           {c['reason']}")
        if c.get("fix"):
            print(f"           → {c['fix']}")
    print(f"\n■ 규칙 검토 ({len(res['findings'])}건, {res['pack']['name']})")
    for f in res["findings"]:
        print(f"  {f['rule_id']} {f['rule_name']}: {f['quote'][:60]}")
        print(f"           {f['why']}\n           → {f['fix']}")
    print(f"\n■ 총평: {res['overall']}")
    u = res["usage"]
    print(f"■ Liner 호출: 모델 {u['model_calls']}회({', '.join(u['models'])}), 검색 {u['search_calls']}회")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
