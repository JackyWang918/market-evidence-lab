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
    def historical_context(self, last=90):
        rows = [row(i, 9.9, 11, 10) for i in range(last+1)]
        rows[20] = row(20, 6, 15, 10)
        data = context(rows)
        data.update(discovered_on=(START+timedelta(days=91)).isoformat(),
                    anchor_date=(START+timedelta(days=89)).isoformat())
        return data

    def test_initialization_includes_confirmed_points_before_anchor_and_before_30_day_window(self):
        result = build_continuation(self.historical_context())
        for plan, level in zip(result["plans"], (15, 6)):
            self.assertEqual(D(plan["zone"]["level"]), D(level))
            self.assertEqual(plan["zone"]["anchor"], (START+timedelta(days=20)).isoformat())
            self.assertTrue(plan["zone"]["confirmed"])

    def test_starting_above_a_boundary_does_not_claim_first_breakout_on_discovery(self):
        data = self.historical_context()
        data["candles"][89] = row(89, 15.7, 16.5, 16)
        data["candles"][90] = row(90, 15.7, 16.4, 16)
        output = build_continuation(data)
        plan = output["plans"][0]
        self.assertTrue(plan["preexisting_break"])
        self.assertEqual(plan["status"], "突破待站稳")
        self.assertTrue(any("开始观察时已在边界外" in e["label"] for e in output["events"]))
        self.assertFalse(any("首次收盘突破" in e["label"] for e in output["events"]))

    def test_old_reference_survives_recent_window_and_atr_changes(self):
        first = build_continuation(self.historical_context())
        data = self.historical_context(130)
        data["candles"][-1] = row(130, 8, 13, 10)
        later = build_continuation(data)
        for original, current in zip(first["plans"], later["plans"]):
            self.assertEqual(current["zone"], original["zone"])
            self.assertEqual(current["created_on"], original["created_on"])

    def test_new_pivot_replaces_reference_only_after_confirmation_is_available(self):
        data = self.historical_context(95)
        data["candles"][92] = row(92, 8, 14, 10)
        early = deepcopy(data)
        early["cutoff"] = (START+timedelta(days=94)).isoformat()
        before = build_continuation(early)
        after = build_continuation(data)
        self.assertEqual(D(before["plans"][0]["zone"]["level"]), D(15))
        self.assertEqual(D(before["plans"][1]["zone"]["level"]), D(6))
        for plan, level in zip(after["plans"], (14, 8)):
            self.assertEqual(D(plan["zone"]["level"]), D(level))
            self.assertEqual(plan["zone"]["available"], (START+timedelta(days=94)).isoformat())

    def test_confirmed_breakout_advances_to_new_higher_high_without_returning_to_old_zone(self):
        rows = base()+[row(20, 12.1, 12.6, 12.3), row(21, 12.2, 12.7, 12.4),
                       row(22, 13, 14, 13.5), row(23, 12.8, 13.3, 13.1),
                       row(24, 12.9, 13.3, 13.2), row(25, 13, 13.7, 13.5)]
        old = build_continuation(context(rows[:25]))
        self.assertEqual(old["plans"][0]["zone"]["level"], "11")
        self.assertIsNotNone(old["plans"][0]["confirmed_on"])
        self.assertEqual(D(old["plans"][0]["next_candidate"]["level"]), D(14))
        updated = build_continuation(context(rows))
        plan = updated["plans"][0]
        self.assertEqual(D(plan["zone"]["level"]), D(14))
        self.assertEqual(plan["zone"]["anchor"], row(22, 1, 2, 1)["time"])
        self.assertEqual(plan["created_on"], row(25, 1, 2, 1)["time"])
        self.assertEqual(plan["status"], "等待向上突破")
        self.assertTrue(any("原区间" in event["label"] for event in updated["events"]))

    def test_unconfirmed_new_high_is_shown_as_candidate_without_moving_formal_zone(self):
        rows = base()+[row(20, 12.1, 12.6, 12.3), row(21, 12.2, 12.7, 12.4)]
        result = build_continuation(context(rows))
        plan = result["plans"][0]
        self.assertEqual(D(plan["zone"]["level"]), D(11))
        self.assertEqual(D(plan["next_candidate"]["level"]), D("12.7"))
        self.assertFalse(plan["next_candidate"]["confirmed"])
        self.assertIn("候选区间暂不作为正式突破条件", plan["next"])

    def test_confirmed_breakdown_advances_to_new_lower_low(self):
        rows = base()+[row(20, 8.6, 9, 8.8), row(21, 8.5, 8.9, 8.7),
                       row(22, 7, 8.3, 7.5), row(23, 8, 8.5, 8.2),
                       row(24, 8, 8.6, 8.3), row(25, 7.9, 8.5, 8.2)]
        updated = build_continuation(context(rows))
        plan = updated["plans"][1]
        self.assertEqual(D(plan["zone"]["level"]), D(7))
        self.assertEqual(plan["created_on"], row(25, 1, 2, 1)["time"])
        self.assertEqual(plan["status"], "等待向下跌破")

    def test_incremental_state_matches_full_replay_including_confirmation_and_failure(self):
        rows = base()+[row(20, 12.1, 12.6, 12.3), row(21, 12.2, 12.7, 12.4),
                       row(22, 12.3, 12.8, 12.5), row(23, 10, 12, 10.5), row(24, 10, 11, 10.5)]
        previous = None
        for count in range(21, len(rows)+1):
            data = context(rows[:count])
            expected = build_continuation(data)
            incremental = build_continuation(data, previous=previous)
            self.assertEqual(incremental, expected)
            previous = incremental

    def test_incremental_and_full_replay_agree_when_higher_level_is_confirmed(self):
        rows = base()+[row(20, 12.1, 12.6, 12.3), row(21, 12.2, 12.7, 12.4),
                       row(22, 13, 14, 13.5), row(23, 12.8, 13.3, 13.1),
                       row(24, 12.9, 13.3, 13.2), row(25, 13, 13.7, 13.5)]
        previous = None
        for count in range(21, len(rows)+1):
            data = context(rows[:count])
            previous = build_continuation(data, previous=previous)
            self.assertEqual(previous, build_continuation(data))

    def test_revised_earlier_inputs_and_older_versions_force_recalculation(self):
        data = self.historical_context()
        previous = build_continuation(data)
        changed = self.historical_context(91)
        changed["candles"][20] = row(20, 5, 16, 10)
        self.assertEqual(build_continuation(changed, previous=previous), build_continuation(changed))
        old = deepcopy(previous)
        old["version"] = "continuation-trial-v1"
        self.assertEqual(build_continuation(changed, previous=old), build_continuation(changed))

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
        original["continuation"]["version"] = "continuation-trial-v1"
        saved = deepcopy(original)
        shown = present_structure(original)
        self.assertEqual(shown["continuation"]["version"], VERSION)
        self.assertEqual(shown["continuation"]["provenance"], "recomputed")
        self.assertEqual(original, saved)


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
