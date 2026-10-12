from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.daily_mover_monitor.attention import VERSION, build_attention
from apps.daily_mover_monitor.structure_presentation import present_structure
from apps.daily_mover_monitor.models import StructureWatch
from apps.daily_mover_monitor.structure_service import observe

START = date(2026, 9, 1)


def row(i, low, high, close, opening=None):
    return {"time": (START+timedelta(days=i)).isoformat(), "low": str(low), "high": str(high),
            "close": str(close), "open": str(close if opening is None else opening)}


def history():
    return [row(i, 9.9, 11, 10) for i in range(18)]+[row(18, 14, 15, 14.5), row(19, 12, 14, 13)]


def context(rows):
    return {"state": "回撤中", "cutoff": rows[-1]["time"], "candles": rows,
            "discovered_on": (START+timedelta(days=21)).isoformat(), "anchor_date": START.isoformat(),
            "peak_date": (START+timedelta(days=18)).isoformat(), "consolidation_days": 0,
            "rules": {"platform_days": 5, "max_width_pct": "12", "max_close_span_pct": "8"},
            "platform": None, "plans": [], "reason": ""}


class AttentionTests(SimpleTestCase):
    def test_no_platform_still_has_long_and_short_observation_zones(self):
        original = context(history()+[row(20, 12, 13, 12.8)])
        saved = deepcopy(original)
        result = build_attention(original)
        self.assertEqual(original, saved)
        self.assertEqual(result["version"], VERSION)
        self.assertTrue(all(p["zone"] for p in result["plans"]))
        self.assertEqual(result["plans"][0]["status"], "观察中")
        self.assertEqual(result["plans"][1]["status"], "等待跌破")
        self.assertTrue(all(p["entry_zone"] is None and p["r"] is None for p in result["plans"]))
        self.assertIn("待验证", result["plans"][0]["zone"]["basis"])

    def test_zone_width_has_price_limits_and_only_uses_prior_candles(self):
        result = build_attention(context(history()+[row(20, 12, 100, 90)]))
        zone = result["plans"][0]["zone"]
        half = D(zone["high"])-D(zone["level"])
        self.assertLessEqual(half/D(zone["level"]), D("0.03"))
        self.assertGreaterEqual(half/D(zone["level"]), D("0.005"))
        self.assertEqual(zone["anchor"], (START+timedelta(days=19)).isoformat())

    def test_future_prices_do_not_change_current_judgment(self):
        original = context(history()+[row(20, 12, 13, 12.8)])
        expected = build_attention(original)
        original["candles"] += [row(21, 1, 200, 100)]
        self.assertEqual(build_attention(original), expected)

    def test_long_requires_later_stop_and_confirmed_rebound_then_freezes_prices(self):
        rows = history()+[row(20, 12, 13, 12.8), row(21, 12.1, 13.5, 13), row(22, 12.2, 13.1, 12.9)]
        self.assertIsNone(build_attention(context(rows))["plans"][0]["entry"])
        rows.append(row(23, 12.3, 14, 13.8))
        plan = build_attention(context(rows))["plans"][0]
        self.assertEqual(plan["status"], "条件成立")
        self.assertEqual(plan["entry"], "13.8")
        self.assertEqual(plan["target"], "15")
        self.assertGreater(D(plan["entry_zone"]["low"]), D(plan["invalid"]))
        rows.append(row(24, 13.9, 16, 15))
        later = build_attention(context(rows))["plans"][0]
        for key in ("zone", "entry_zone", "entry", "invalid", "target", "r", "triggered_at"):
            self.assertEqual(later[key], plan[key])

    def test_short_cannot_trigger_on_breakdown_it_requires_later_failed_retest(self):
        rows = history()+[row(20, 10.8, 12, 11)]
        plan = build_attention(context(rows))["plans"][1]
        self.assertEqual(plan["status"], "等待反抽")
        self.assertIsNone(plan["entry_zone"])
        rows.append(row(21, 10.9, 12.1, 11.2, 12))
        result = build_attention(context(rows))["plans"][1]
        self.assertEqual(result["status"], "条件成立")
        self.assertEqual(result["entry"], "11.2")
        self.assertGreater(D(result["invalid"]), D(result["entry_zone"]["high"]))
        self.assertLess(D(result["target"]), D(result["entry"]))
        expected = (D(result["entry"])-D(result["target"]))/ (D(result["invalid"])-D(result["entry"]))
        self.assertEqual(D(result["r"]), expected)

    def test_short_close_reclaims_region_invalidates_breakdown(self):
        rows = history()+[row(20, 10.8, 12, 11), row(21, 12.5, 14, 13)]
        plan = build_attention(context(rows))["plans"][1]
        self.assertEqual(plan["status"], "已失效")
        self.assertIsNone(plan["r"])

    def test_triggered_short_keeps_failure_even_if_price_drops_again(self):
        rows = history()+[row(20, 10.8, 12, 11), row(21, 10.9, 12.1, 11.2, 12), row(22, 12.5, 14, 13), row(23, 10, 12, 11)]
        plan = build_attention(context(rows))["plans"][1]
        self.assertEqual(plan["status"], "已失效")
        self.assertEqual(plan["failed_on"], (START+timedelta(days=22)).isoformat())
        self.assertIsNone(plan["r"])

    def test_short_retest_closing_bullish_does_not_trigger(self):
        rows = history()+[row(20, 10.8, 12, 11), row(21, 10.9, 12.1, 11.2, 11)]
        self.assertIsNone(build_attention(context(rows))["plans"][1]["entry"])

    def test_insufficient_and_invalid_data_have_no_invented_prices(self):
        for rows in (history()[:10], history()[:5]+history()[6:]):
            result = build_attention(context(rows))
            self.assertTrue(all(p["zone"] is None for p in result["plans"]))
        rows = history()+[row(20, 12, 13, 12.8)]
        rows[-1]["close"] = "NaN"
        self.assertTrue(all(p["zone"] is None for p in build_attention(context(rows))["plans"]))

    def test_legacy_presentation_labels_recalculation_and_keeps_original(self):
        original = context(history()+[row(20, 12, 13, 12.8)])
        saved = deepcopy(original)
        presentation = present_structure(original)
        self.assertEqual(presentation["attention"]["provenance"], "recomputed")
        self.assertEqual(original, saved)
        original["attention"] = deepcopy(presentation["attention"])
        original["attention"]["provenance"] = "saved"
        self.assertEqual(present_structure(original)["attention"]["provenance"], "saved")


class AttentionPageTests(TestCase):
    def test_detail_and_pool_show_two_directions_even_without_platform(self):
        rows = history()+[row(20, 12, 13, 12.8)]
        watch = StructureWatch.objects.create(symbol="TESTUSDT", quote_asset="USDT", origin="official",
            discovered_on=START+timedelta(days=21), last_ranked_on=START+timedelta(days=21), anchor_date=START+timedelta(days=18), anchor_price=14)
        # Normal publication saves attention with its own version and immutable input hash.
        observation = observe(watch, watch.discovered_on, rows)
        self.assertEqual(observation.result["attention"]["version"], VERSION)
        detail = self.client.get(reverse("daily_mover_monitor:detail", args=[watch.symbol]), {"date": watch.discovered_on.isoformat()})
        self.assertContains(detail, "多空注意力区间")
        self.assertContains(detail, "多头止跌确认")
        self.assertContains(detail, "空头跌破反抽")
        self.assertContains(detail, "确认后计算")
        self.assertContains(self.client.get(reverse("daily_mover_monitor:watchlist")), "回撤低点")
