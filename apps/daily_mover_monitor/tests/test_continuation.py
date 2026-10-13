from copy import deepcopy
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.daily_mover_monitor.continuation import VERSION, build_continuation
from apps.daily_mover_monitor.structure_presentation import present_structure
from apps.daily_mover_monitor.structure_service import observe
from apps.daily_mover_monitor.models import StructureWatch, StructureObservation
from .test_attention import START, row, context
from datetime import timedelta


def base():
    return [row(i, 9.9, 11, 10) for i in range(20)]


class ContinuationTests(SimpleTestCase):
    def test_high_and_low_have_independent_boundaries(self):
        plans = build_continuation(context(base()+[row(20, 10, 11, 10.5)]))["plans"]
        self.assertEqual(D(plans[0]["zone"]["level"]), D(11))
        self.assertEqual(D(plans[1]["zone"]["level"]), D("9.9"))
        self.assertNotEqual(plans[0]["zone"], plans[1]["zone"])
        self.assertIn("待验证", plans[0]["zone"]["basis"])

    def test_two_closes_confirm_and_three_outside_candles_form_sideways_symmetrically(self):
        for direction, bars, first_status, hold_status, sideways_status in (
            (0, [(12.1, 12.6, 12.3), (12.2, 12.7, 12.4), (12.3, 12.8, 12.5)], "突破待站稳", "向上站稳", "高位横盘"),
            (1, [(8.6, 9, 8.8), (8.5, 8.9, 8.7), (8.4, 8.8, 8.6)], "跌破待站稳", "向下保持", "低位横盘"),
        ):
            rows = base()
            for j, values in enumerate(bars):
                rows.append(row(20+j, *values))
                plan = build_continuation(context(rows))["plans"][direction]
                self.assertEqual(plan["status"], (first_status, hold_status, sideways_status)[j])
                self.assertEqual(plan["outside_days"], j+1)
                if j == 0:
                    zone = plan["zone"]
                    self.assertIsNone(plan["confirmed_on"])
                else:
                    self.assertEqual(plan["zone"], zone)
            self.assertIsNotNone(plan["sideways"])

    def test_intraday_cross_and_inside_sideways_are_not_breakouts(self):
        result = build_continuation(context(base()+[row(20, 9, 14, 10.5)]))
        self.assertTrue(all(p["broken_on"] is None for p in result["plans"]))
        rows = base()+[row(i, 10, 10.8, 10.5) for i in range(20, 23)]
        self.assertTrue(all(p["sideways"] is None for p in build_continuation(context(rows))["plans"]))

    def test_return_inside_fails_and_preserves_event_on_rebuild(self):
        rows = base()+[row(20, 12, 12.6, 12.3), row(21, 10, 12, 10.5)]
        result = build_continuation(context(rows))
        self.assertEqual(result["plans"][0]["status"], "突破未守住")
        self.assertIsNone(result["plans"][0]["confirmed_on"])
        rows.append(row(22, 10, 11, 10.5))
        later = build_continuation(context(rows))
        self.assertTrue(any("未守住" in e["label"] for e in later["events"]))
        self.assertIsNone(later["plans"][0]["failed_on"])

    def test_retest_is_separate_from_entirely_outside_sideways(self):
        for direction, bars, status in (
            (0, [(12, 12.6, 12.3), (11, 12.7, 12.4)], "回踩守住"),
            (1, [(8.6, 9, 8.8), (8.5, 10, 8.7)], "反抽未收复"),
        ):
            rows = base()+[row(20+i, *v) for i, v in enumerate(bars)]
            plan = build_continuation(context(rows))["plans"][direction]
            self.assertEqual(plan["status"], status)
            self.assertIsNotNone(plan["retest_on"])
            self.assertIsNone(plan["sideways"])

    def test_wide_range_is_not_sideways_even_when_all_closes_are_outside(self):
        rows = base()+[row(20, 12, 14, 13), row(21, 12, 15, 13), row(22, 12, 16, 13)]
        plan = build_continuation(context(rows))["plans"][0]
        self.assertEqual(plan["status"], "向上站稳")
        self.assertIsNone(plan["sideways"])

    def test_current_and_future_candles_do_not_change_initial_boundary(self):
        original = context(base()+[row(20, 10, 11, 10.5)])
        expected = build_continuation(original)
        changed = deepcopy(original)
        changed["candles"][-1] = row(20, 1, 100, 50)
        actual = build_continuation(changed)
        for a, b in zip(expected["plans"], actual["plans"]):
            self.assertEqual(a["zone"], b["zone"])
        original["candles"].append(row(21, 1, 100, 50))
        self.assertEqual(build_continuation(original), expected)

    def test_platform_only_used_after_confirmation(self):
        data = context(base()+[row(20, 10, 11, 10.5)])
        data["platform"] = {"low": "10", "high": "11", "start": row(18, 1, 2, 1)["time"], "confirmed": data["cutoff"]}
        self.assertNotIn("平台", build_continuation(data)["plans"][0]["zone"]["basis"])
        data["platform"]["confirmed"] = row(19, 1, 2, 1)["time"]
        result = build_continuation(data)
        self.assertEqual(result["plans"][0]["zone"]["basis"], "已确认平台上沿")
        self.assertEqual(result["plans"][1]["zone"]["basis"], "已确认平台下沿")

    def test_pivots_require_two_completed_right_candles(self):
        rows = base()+[row(20, 10, 15, 12), row(21, 10.5, 13, 12), row(22, 11, 13, 12)]
        data = context(rows)
        data["discovered_on"] = (START+timedelta(days=23)).isoformat()
        self.assertIn("待验证", build_continuation(data)["plans"][0]["zone"]["basis"])
        data["candles"].append(row(23, 11, 13, 12))
        data["cutoff"] = data["candles"][-1]["time"]
        plan = build_continuation(data)["plans"][0]
        self.assertEqual(plan["zone"]["basis"], "最近已确认日线高点")
        self.assertEqual(plan["zone"]["available"], row(22, 1, 2, 1)["time"])

    def test_invalid_and_missing_data_do_not_invent_boundaries(self):
        for rows in (base()[:10], base()+[row(20, 10, 9, 10)], base()+[row(21, 10, 11, 10)]):
            self.assertTrue(all(p["zone"] is None for p in build_continuation(context(rows))["plans"]))

    def test_legacy_recalculation_does_not_mutate_original(self):
        original = context(base()+[row(20, 10, 11, 10.5)])
        saved = deepcopy(original)
        shown = present_structure(original)
        self.assertEqual(shown["continuation"]["provenance"], "recomputed")
        self.assertEqual(original, saved)
        original["continuation"] = deepcopy(shown["continuation"])
        original["continuation"]["provenance"] = "saved"
        self.assertEqual(present_structure(original)["continuation"]["provenance"], "saved")


class ContinuationPublicationTests(TestCase):
    def test_publication_saves_new_rule_and_legacy_page_labels_recalculation(self):
        watch = StructureWatch.objects.create(symbol="TESTUSDT", quote_asset="USDT", origin="official",
            discovered_on=START+timedelta(days=21), last_ranked_on=START+timedelta(days=21), anchor_date=START, anchor_price=10)
        observation = observe(watch, watch.discovered_on, base()+[row(20, 10, 11, 10.5)])
        self.assertEqual(observation.result["continuation"]["version"], VERSION)
        self.assertEqual(observation.result["continuation"]["provenance"], "saved")
        original = deepcopy(observation.result)
        original.pop("continuation")
        legacy_day = watch.discovered_on+timedelta(days=1)
        legacy = StructureObservation.objects.create(watch=watch, as_of_date=legacy_day,
            rule_version=observation.rule_version, data_cutoff_at=observation.data_cutoff_at,
            input_hash=observation.input_hash, result=original)
        response = self.client.get(reverse("daily_mover_monitor:detail", args=[watch.symbol]), {"date": legacy_day.isoformat()})
        self.assertContains(response, "新规则按原记录留存日 K 回算")
        legacy.refresh_from_db()
        self.assertEqual(legacy.result, original)
