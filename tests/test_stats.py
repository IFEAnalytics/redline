"""통계 대조의 계산 규칙 — 네트워크 없이 고정 자료로 확인한다."""
import pytest

from redline import stats


@pytest.fixture(autouse=True)
def fake_fred(monkeypatch):
    data = {
        # 2025-10 이 빠진 월별 지수(실제 CPI 처럼 결측 월이 있는 경우)
        "CPIAUCSL": [("2025-08-01", 100.0), ("2025-09-01", 101.0), ("2025-11-01", 102.0)]
        + [(f"2026-{m:02d}-01", 103.0 + m * 0.1) for m in range(1, 10)],
        "UNRATE": [("2026-08-01", 4.1), ("2026-09-01", 4.2)],
        "GDPC1": [("2026-01-01", 100.0), ("2026-04-01", 101.0)],
        "DGS10": [("2026-10-29", 4.90), ("2026-10-30", 4.94), ("2026-11-02", 4.80)],
    }
    monkeypatch.setattr(stats, "fetch", lambda sid: tuple(data[sid]))


def test_yoy_uses_calendar_not_row_offset():
    o = stats.observe("CPIAUCSL", "yoy", "2026-09")
    assert "2025-09-01" in o.detail
    assert o.value == pytest.approx((103.9 / 101.0 - 1) * 100, abs=1e-3)


def test_missing_comparison_month_is_an_error():
    with pytest.raises(ValueError):
        stats.observe("CPIAUCSL", "yoy", "2026-10")  # 2026-10 관측 자체가 없다


def test_change_and_level():
    assert stats.observe("UNRATE", "level", "2026-09").value == 4.2
    o = stats.observe("UNRATE", "chg", "2026-09")
    assert o.value == pytest.approx(0.1) and o.unit == "%p"


def test_quarterly_annualized():
    assert stats.observe("GDPC1", "saar", "2026-Q2").value == pytest.approx((1.01 ** 4 - 1) * 100, abs=1e-3)


def test_daily_month_end_and_exact_day():
    assert stats.observe("DGS10", "level", "2026-10").value == 4.94
    assert stats.observe("DGS10", "level", "2026-10-29").value == 4.90


def test_compare_tolerates_rounding_only():
    assert stats.compare(3.1, 3.12, "yoy")[0] == "match"
    assert stats.compare(2.6, 3.12, "yoy")[0] == "mismatch"
    assert stats.compare(60, 60.0, "chg")[0] == "match"
    assert stats.compare(0.0, 0.1, "chg")[0] == "mismatch"


def test_period_parsing():
    assert stats.parse_period("2026-Q3") == ("Q", "2026-07-01")
    assert stats.parse_period("2026-9") == ("M", "2026-09-01")
    assert stats.parse_period("작년") is None
