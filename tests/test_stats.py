"""통계 대조의 계산 규칙 — 네트워크 없이 고정 자료로 확인한다."""
import pytest

from redline import stats


@pytest.fixture(autouse=True)
def fake_fred(monkeypatch):
    data = {
        # 2025-10 이 빠진 월별 지수(실제 CPI 처럼 결측 월이 있는 경우)
        "CPIAUCNS": [("2025-08-01", 100.0), ("2025-09-01", 101.0), ("2025-11-01", 102.0)]
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


def test_july_cpi_uses_published_basis(monkeypatch):
    # BLS July 2026 release, Table 1, and the corresponding adjusted series.
    series = {"CPIAUCNS": (("2025-07-01", 323.048), ("2026-07-01", 333.918)),
              "CPIAUCSL": (("2025-07-01", 322.169), ("2026-06-01", 332.568), ("2026-07-01", 332.813))}
    monkeypatch.setattr(stats, "fetch", series.__getitem__)
    annual = stats.observe("CPIAUCSL", "yoy", "2026-07")
    assert annual.series_id == "CPIAUCNS" and annual.url.endswith("/CPIAUCNS")
    assert stats.display_value(annual) == "3.4"
    assert stats.display_value(stats.observe("CPIAUCSL", "mom", "2026-07")) == "0.1"
    assert stats.display_value(stats.observe("CPIAUCSL", "yoy", "2026-07", adjustment="sa")) == "3.3"


def test_daily_series_requires_evidence_for_requested_period():
    for period in ("2026-12", "2026-10-31", "2026-11"):
        with pytest.raises(ValueError):
            stats.observe("DGS10", "level", period, as_of="2027-01-01")
    with pytest.raises(ValueError):
        stats.observe("DGS10", "level", "2026-10", as_of="2026-10-29")
    point = stats.observe("DGS10", "level", "2026-10", as_of="2026-11-01")
    assert stats.display_value(point) == "4.94"
    assert stats.compare(float(stats.display_value(point)), point.value, "level")[0] == "match"
    assert stats.compare(159700, 160000, "level")[0] == "mismatch"


@pytest.mark.parametrize("claimed, observed, places", [(3.4, 3.347, 1), (0.2, 0.145, 1), (3.42, 3.3648, 2), (3.40, 3.3648, 2)])
def test_rounding_cannot_certify_a_different_printed_value(claimed, observed, places):
    assert stats.compare(claimed, observed, "yoy", places=places)[0] == "mismatch"


def test_period_must_match_frequency_and_as_of():
    for sid, period in [("UNRATE", "2026-Q3"), ("GDPC1", "2026-08"), ("UNRATE", "2026-09-15")]:
        with pytest.raises(ValueError, match="관측 주기"):
            stats.observe(sid, "level" if sid == "UNRATE" else "yoy", period)
    with pytest.raises(ValueError, match="끝나지"):
        stats.observe("UNRATE", "level", "2026-09", as_of="2026-09-15")


def test_missing_prior_and_core_nsa(monkeypatch):
    monkeypatch.setattr(stats, "fetch", {"CPILFENS": (("2026-07-01", 337.133),)}.__getitem__)
    with pytest.raises(ValueError, match="비교 시점"):
        stats.observe("CPILFESL", "yoy", "2026-07")
    monkeypatch.setattr(stats, "fetch", {"CPILFENS": (("2025-07-01", 328.980), ("2026-07-01", 337.133))}.__getitem__)
    o = stats.observe("CPILFESL", "yoy", "2026-07")
    assert o.series_id == "CPILFENS" and stats.display_value(o) == "2.5"


def test_explicit_trailing_zero_and_oecd_near_match():
    from redline.pipeline import _decimal_places
    assert _decimal_places(3.4, "물가는 3.40% 올랐다.", 3.4) == 2
    assert stats.compare(3.4, 3.347, "yoy", loose=True, places=1)[0] == "near"


def test_no_change_claim_is_not_confirmed_by_integer_rounding():
    # "실업률은 전월과 같았다"(0) 인데 실제로는 0.1%p 올랐다
    assert stats.compare(0.0, 0.1, "chg", places=0)[0] == "mismatch"
    assert stats.compare(0.0, 0.0, "chg", places=0)[0] == "match"


def test_headcount_written_in_ten_thousands_compares_at_that_precision():
    from redline.pipeline import _decimal_places
    assert _decimal_places("190", "8월 비농업 취업자는 19만 명 늘었다.", 190.0) == -1
    assert stats.compare(190.0, 186.0, "chg", places=-1)[0] == "match"
    assert stats.compare(190.0, 60.0, "chg", places=-1)[0] == "mismatch"
    assert _decimal_places("186", "8월 비농업 취업자는 18만 6천 명 늘었다.", 186.0) == 0
    assert stats.compare(186.0, 186.0, "chg", places=0)[0] == "match"


def test_display_uses_manuscript_precision():
    o = stats.Observed("X", "level", "2026-Q2", "2026-04-01", 0.633844, "%", "", "")
    assert stats.display_value(o, 1) == "0.6"
    o = stats.Observed("X", "chg", "2026-09", "2026-09-01", 0.1, "%p", "", "")
    assert stats.display_value(o, 0) == "0.1"
    o = stats.Observed("X", "chg", "2026-09", "2026-09-01", 60.0, "천 명", "", "")
    assert stats.display_value(o, 0) == "60"
