from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.daily_mover_monitor.detector import Candle
from apps.daily_mover_monitor.models import TrendObservation, TrendWatch
from apps.daily_mover_monitor.trend import analyze, candidate_reason
from apps.daily_mover_monitor.trend_service import update_official
from apps.daily_mover_monitor.trend_presentation import chart_indicators


DAY = date(2026, 10, 12)


def rows_for(day, *, next_close=None):
    start = day - timedelta(days=35)
    rows = []
    for index in range(35):
        close = Decimal("12") if index == 34 else Decimal("10")
        rows.append(Candle(datetime.combine(start + timedelta(days=index), datetime.min.time(), UTC),
                           close, close * Decimal("1.01"), close * Decimal("0.99"), close,
                           Decimal("200000")))
    if next_close is not None:
        close = Decimal(next_close)
        rows.append(Candle(datetime.combine(day, datetime.min.time(), UTC),
                           close, close * Decimal("1.01"), close * Decimal("0.99"), close,
                           Decimal("200000")))
    return rows


class TrendRuleTests(SimpleTestCase):
    def test_chart_overlay_distinguishes_close_high_from_wick_and_confirmed_low(self):
        sample = [
            {"date": (DAY - timedelta(days=33-index)).isoformat(),
             "close": str(Decimal("12") if index == 32 else Decimal("10")),
             "low": str(Decimal("8") if index == 29 else Decimal("9"))}
            for index in range(33)
        ]
        overlay = chart_indicators(sample)
        self.assertTrue(overlay[-1]["high30"])
        self.assertEqual(overlay[-1]["recent14_high_count"], 1)
        self.assertTrue(overlay[29]["pivot_low"])
        self.assertFalse(chart_indicators(sample[:31])[-2]["pivot_low"])

    def test_first_close_breakout_precedes_old_mover_threshold(self):
        rows = rows_for(DAY)
        self.assertIsNotNone(candidate_reason(rows))
        result = analyze(rows, cutoff=DAY - timedelta(days=1))
        self.assertEqual(result["stage"], "初次扩张")
        self.assertTrue(result["new_close_high_30d"])
        self.assertEqual(result["center_10d"], "10")

    def test_future_bar_cannot_change_prior_judgment(self):
        rows = rows_for(DAY, next_close="30")
        past = analyze(rows, cutoff=DAY - timedelta(days=1))
        self.assertEqual(past, analyze(rows[:-1], cutoff=DAY - timedelta(days=1)))

    def test_candidate_rejects_short_history_and_thin_turnover(self):
        rows = rows_for(DAY)
        self.assertIsNone(candidate_reason(rows[-20:]))
        self.assertIsNotNone(candidate_reason(rows[-21:]))
        self.assertIsNone(analyze(rows[-21:], cutoff=DAY - timedelta(days=1))["new_close_high_30d"])
        rows[-1] = Candle(rows[-1].open_at, rows[-1].open, rows[-1].high,
                          rows[-1].low, rows[-1].close, None)
        self.assertIsNone(candidate_reason(rows))


class FakeSource:
    def symbols(self):
        return [("EARLYUSDT", "USDT")]

    def candles(self, symbol, day, *, days=90):
        return rows_for(DAY, next_close="11.8" if day > DAY else None)

    def depth(self, symbol):
        return Decimal("20000"), Decimal("20000"), Decimal("5")


class TrendServiceTests(TestCase):
    def test_discovers_without_board_and_tracks_after_breakout(self):
        source = FakeSource()
        count, errors = update_official(DAY, client=source)
        self.assertEqual((count, errors), (1, []))
        watch = TrendWatch.objects.get()
        self.assertEqual(watch.discovered_on, DAY)
        self.assertFalse(TrendObservation.objects.get().result["ranked"])
        self.assertEqual(update_official(DAY, client=source), (0, []))
        count, errors = update_official(DAY + timedelta(days=1), client=source)
        self.assertEqual((count, errors), (1, []))
        self.assertEqual(TrendWatch.objects.count(), 1)
        self.assertEqual(TrendObservation.objects.count(), 2)
        self.assertEqual(watch.observations.first().result["stage"], "高位整理")
        listing = self.client.get(reverse("daily_mover_monitor:trend_list"),
                                  {"date": (DAY + timedelta(days=1)).isoformat()})
        self.assertContains(listing, "EARLYUSDT")
        self.assertContains(listing, "近 30 根日 K 收盘变化")
        detail = self.client.get(reverse("daily_mover_monitor:trend_detail", args=["EARLYUSDT"]),
                                 {"date": (DAY + timedelta(days=1)).isoformat()})
        self.assertContains(detail, "每日阶段与事件")
        self.assertContains(detail, 'id="dm-trend-candles"')
        self.assertContains(detail, 'id="dm-trend-indicator-data"')
        self.assertContains(detail, "对数价格轴")
        self.assertContains(detail, "日成交额")
