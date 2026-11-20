"""채점 규칙 — 표시 구간이 정답 문장과 겹치는지로 센다."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("evalrun", Path(__file__).resolve().parent.parent / "eval" / "run.py")
evalrun = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evalrun)

CASE = {"id": "t", "labels": [
    {"kind": "fact_value", "span": [0, 10], "sentence": "a"},
    {"kind": "rule", "span": [11, 20], "sentence": "b", "rule_id": "R10"},
    {"kind": "control", "span": [21, 30], "sentence": "c"},
    {"kind": "control", "span": [31, 40], "sentence": "d"},
]}


def rows(claims, findings):
    return {r["sentence"]: r for r in evalrun.score_case(CASE, {"claims": claims, "findings": findings})}


def test_fact_needs_a_mismatch_on_that_sentence():
    r = rows([{"status": "confirmed", "span": [0, 10]}], [{"rule_id": "R01", "span": [2, 5]}])
    assert r["a"]["caught"] is False  # 규칙 표시만으로는 사실 오류 적발이 아니다
    r = rows([{"status": "mismatch", "span": [3, 8]}], [])
    assert r["a"]["caught"] is True


def test_rule_exact_requires_same_rule_id():
    r = rows([], [{"rule_id": "R03", "span": [11, 15]}])
    assert r["b"]["caught"] is True and r["b"]["exact"] is False
    r = rows([], [{"rule_id": "R10", "span": [11, 15]}])
    assert r["b"]["exact"] is True


def test_control_false_alarm_and_noise_are_separate():
    r = rows([{"status": "mismatch", "span": [21, 30]}], [{"rule_id": "R01", "span": [31, 40]}])
    assert r["c"]["false_alarm"] is True
    assert r["d"]["false_alarm"] is False and r["d"]["marked"] is True


def test_adjacent_spans_do_not_overlap():
    assert not evalrun.overlap([0, 10], [10, 20])
    assert not evalrun.overlap(None, [0, 5])
