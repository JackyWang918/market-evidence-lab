from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.daily_mover_monitor.detector import Candle
from apps.daily_mover_monitor.models import DailyMoverSchedule, DailyMoverSnapshot, StructureObservation, StructureWatch
from apps.daily_mover_monitor.service import _update_structures
from apps.daily_mover_monitor.structure import VERSION, analyze, space
from apps.daily_mover_monitor.structure_service import observe, replay, update_official
from apps.daily_mover_monitor.structure_presentation import present_structure

START = date(2026, 9, 1)


def row(day, low, high, close):
    return {"time": day.isoformat(), "open": str(close), "high": str(high), "low": str(low), "close": str(close)}


def platform_rows():
    return [row(START, 10, 11, 10), row(START+timedelta(days=1), 18, 25, 23)] + [
        row(START+timedelta(days=i), 19, 21, 20) for i in range(2, 7)]


def evaluate(rows, cutoff=None):
    return analyze(rows, cutoff=cutoff or date.fromisoformat(rows[-1]["time"]), anchor_date=START, anchor_price="10")


class CausalStructureTests(SimpleTestCase):
    def test_platform_explanation_does_not_modify_frozen_result_or_use_suffix(self):
        from copy import deepcopy
        rows = platform_rows()[:2]+[row(START+timedelta(days=2), 19, 22, 20)]
        original = evaluate(rows)
        saved = deepcopy(original)
        display = present_structure(original)["platform_view"]
        self.assertIn("1/5", display["summary"])
        self.assertIn("15.79%", display["summary"])
        self.assertIsNone(display["low"])
        self.assertEqual(original, saved)
        original["candles"].append(row(START+timedelta(days=3), 1, 100, 99))
        self.assertEqual(present_structure(original)["platform_view"], display)

    def test_forming_range_is_provisional_and_never_generates_plan_prices(self):
        original = evaluate(platform_rows()[:4])
        display = present_structure(original)
        self.assertEqual(display["platform_view"]["tone"], "provisional")
        self.assertEqual(display["platform_view"]["low"], "19")
        self.assertEqual(display["platform_view"]["high"], "21")
        self.assertIsNone(display["platform"])
        self.assertTrue(all(p["entry"] is None for p in display["plans"]))

    def test_confirmed_and_failed_ranges_keep_their_original_bounds(self):
        confirmed = present_structure(evaluate(platform_rows()))["platform_view"]
        failed = present_structure(evaluate(platform_rows()+[row(START+timedelta(days=7), 17, 20, 18)]))["platform_view"]
        self.assertEqual(confirmed["tone"], "confirmed")
        self.assertEqual(failed["tone"], "failed")
        self.assertEqual(failed["low"], confirmed["low"])
        self.assertIn("失效于", failed["summary"])

    def test_confirmation_bar_is_not_a_breakout_and_bounds_are_frozen(self):
        rows = platform_rows()
        original = evaluate(rows)
        self.assertEqual(original["state"], "平台暂时成立")
        self.assertEqual(original["platform"]["low"], "19")
        self.assertEqual(original["plans"][1]["status"], "等待确认")
        rows.append(row(START+timedelta(days=7), 20, 24, 22))
        later = evaluate(rows)
        self.assertEqual(later["state"], "向上突破")
        self.assertEqual(later["platform"], original["platform"])
        self.assertEqual(later["plans"][1]["entry"], "22")
        self.assertEqual(later["plans"][1]["pressure"], "25")

    def test_future_suffix_does_not_change_historical_output(self):
        rows = platform_rows()
        cutoff = date.fromisoformat(rows[-1]["time"])
        expected = evaluate(rows)
        rows += [row(START+timedelta(days=7), 1, 100, 99)]
        self.assertEqual(evaluate(rows, cutoff), expected)

    def test_observation_touch_does_not_trigger_pullback(self):
        rows = platform_rows()+[row(START+timedelta(days=7), 19, 20, 19.5)]
        result = evaluate(rows)
        self.assertEqual(result["plans"][0]["status"], "等待确认")
        self.assertIsNone(result["plans"][0]["entry"])
        self.assertFalse(any(e["label"] == "回撤确认条件成立" for e in result["events"]))

    def test_pullback_requires_confirmed_rebound_and_uses_own_low(self):
        rows = platform_rows()+[
            row(START+timedelta(days=7), 19, 19.8, 19.5),
            row(START+timedelta(days=8), 19.1, 20.5, 20),
            row(START+timedelta(days=9), 19.2, 20, 19.8),
            row(START+timedelta(days=10), 19.3, 21, 20.8),
        ]
        before = evaluate(rows[:-1])
        self.assertNotEqual(before["plans"][0]["status"], "条件成立")
        result = evaluate(rows)
        self.assertEqual(result["plans"][0]["status"], "条件成立")
        self.assertEqual(result["plans"][0]["entry"], "20.8")
        self.assertEqual(result["plans"][0]["invalid"], "18.905")
        self.assertNotEqual(result["plans"][1]["status"], "条件成立")

    def test_wick_break_is_not_close_failure_and_failure_is_not_erased(self):
        rows = platform_rows()+[row(START+timedelta(days=7), 17, 20, 19.5)]
        self.assertEqual(evaluate(rows)["state"], "平台暂时成立")
        rows += [row(START+timedelta(days=8), 17, 20, 18), row(START+timedelta(days=9), 19, 24, 23)]
        result = evaluate(rows)
        self.assertEqual(result["state"], "平台失效")
        self.assertEqual(result["failure_date"], (START+timedelta(days=8)).isoformat())
        self.assertEqual(result["plans"][1]["status"], "已失效")

    def test_retest_is_a_separate_later_event(self):
        rows = platform_rows()+[row(START+timedelta(days=7), 20, 24, 22)]
        self.assertIsNone(evaluate(rows)["plans"][1]["retest_at"])
        rows += [row(START+timedelta(days=8), 21, 23, 22)]
        self.assertEqual(evaluate(rows)["plans"][1]["retest_at"], (START+timedelta(days=8)).isoformat())

    def test_trigger_prices_and_r_remain_frozen_after_later_prices(self):
        rows = platform_rows()+[row(START+timedelta(days=7), 20, 24, 22)]
        plan = evaluate(rows)["plans"][1]
        rows += [row(START+timedelta(days=8), 21, 30, 29)]
        later = evaluate(rows)["plans"][1]
        for key in ("entry", "invalid", "pressure", "space", "triggered_at"):
            self.assertEqual(later[key], plan[key])

    def test_pullback_failure_does_not_automatically_destroy_platform(self):
        rows = platform_rows()+[
            row(START+timedelta(days=7), 19.2, 19.8, 19.5),
            row(START+timedelta(days=8), 19.3, 20.5, 20),
            row(START+timedelta(days=9), 19.4, 20, 19.8),
            row(START+timedelta(days=10), 19.5, 21, 20.8),
            row(START+timedelta(days=11), 19, 20, 19.05),
        ]
        result = evaluate(rows)
        self.assertEqual(result["plans"][0]["status"], "已失效")
        self.assertEqual(result["plans"][1]["status"], "等待确认")
        self.assertEqual(result["state"], "平台暂时成立")

    def test_missing_latest_day_gap_and_bad_ohlc_are_unavailable(self):
        rows = platform_rows()
        self.assertEqual(evaluate(rows, START+timedelta(days=8))["state"], "数据不足")
        self.assertEqual(evaluate(rows[:3]+rows[4:])["state"], "数据不足")
        rows[-1]["low"] = "99"
        self.assertEqual(evaluate(rows)["state"], "数据不足")

    def test_sharp_decline_is_not_platform_forming(self):
        rows = platform_rows()[:2]+[row(START+timedelta(days=2), 15, 23, 16), row(START+timedelta(days=3), 10, 16, 11)]
        self.assertEqual(evaluate(rows)["state"], "回撤中")

    def test_r_is_price_space_and_missing_pressure_is_not_fabricated(self):
        self.assertEqual(Decimal(space("0.19", "0.175", "0.22")["r"]), Decimal(2))
        self.assertIsNone(space("0.19", "0.19", "0.22")["r"])
        self.assertEqual(space("0.19", "0.175", None)["reason"], "未识别上方压力")
        rows = platform_rows()+[row(START+timedelta(days=7), 20, 27, 26)]
        self.assertIsNone(evaluate(rows)["plans"][1]["pressure"])


class WatchlistStatusFilterTests(TestCase):
    def setUp(self):
        self.day = START+timedelta(days=9)
        for symbol, stopped in (("BTWUSDT", False), ("ENDEDUSDT", True)):
            watch = StructureWatch.objects.create(
                symbol=symbol, quote_asset="USDT", origin="official",
                discovered_on=START+timedelta(days=7), last_ranked_on=self.day,
                anchor_date=START, anchor_price=10,
                ended_on=self.day if stopped else None,
                end_reason="测试停止原因" if stopped else "",
            )
            observe(watch, self.day, platform_rows()+[
                row(START+timedelta(days=i), 19, 21, 20) for i in (7, 8)
            ], ranked=not stopped)

    def test_default_only_shows_ongoing_watches(self):
        response = self.client.get(reverse("daily_mover_monitor:watchlist"), {"date": self.day.isoformat()})
        self.assertEqual([r["watch"].symbol for r in response.context["rows"]], ["BTWUSDT"])
        self.assertContains(response, "在榜跟踪中")
        self.assertNotContains(response, "ENDEDUSDT")

    def test_stopped_filter_excludes_in_board_active_symbol(self):
        response = self.client.get(reverse("daily_mover_monitor:watchlist"), {"date": self.day.isoformat(), "archive": "1"})
        self.assertEqual([r["watch"].symbol for r in response.context["rows"]], ["ENDEDUSDT"])
        self.assertNotContains(response, "BTWUSDT")
        self.assertContains(response, "已停止跟踪")
        self.assertNotContains(response, "数据待更新")
        combined = self.client.get(reverse("daily_mover_monitor:watchlist"), {"date": self.day.isoformat(), "archive": "1", "dropped": "1"})
        self.assertEqual([r["watch"].symbol for r in combined.context["rows"]], ["ENDEDUSDT"])

    def test_stopped_state_is_relative_to_selected_date(self):
        historical = (self.day-timedelta(days=1)).isoformat()
        active = self.client.get(reverse("daily_mover_monitor:watchlist"), {"date": historical})
        self.assertEqual(len(active.context["rows"]), 2)
        stopped = self.client.get(reverse("daily_mover_monitor:watchlist"), {"date": historical, "archive": "1"})
        self.assertEqual(stopped.context["rows"], [])
        self.assertContains(stopped, "没有符合筛选条件的跟踪记录")


class StructurePersistenceTests(TestCase):
    def setUp(self):
        self.day = START+timedelta(days=7)
        self.watch = StructureWatch.objects.create(
            symbol="TESTUSDT", quote_asset="USDT", origin="official", discovered_on=self.day,
            last_ranked_on=self.day, anchor_date=START, anchor_price=10,
        )

    def test_saved_judgment_is_immutable_and_idempotent(self):
        original = observe(self.watch, self.day, platform_rows(), ranked=True)
        again = observe(self.watch, self.day, [], ranked=False)
        self.assertEqual(original.pk, again.pk)
        self.assertEqual(original.input_hash, again.input_hash)
        with self.assertRaises(RuntimeError):
            original.save()
        with self.assertRaises(RuntimeError):
            StructureObservation.objects.all().update(result={})

    def test_dropped_symbol_updates_and_detail_remains_accessible(self):
        observe(self.watch, self.day, platform_rows(), ranked=True)
        next_day = self.day+timedelta(days=1)
        DailyMoverSnapshot.objects.create(as_of_date=next_day, candle_open_at=datetime.combine(next_day-timedelta(days=1), datetime.min.time(), UTC), scanned_count=1, candidate_count=0, excluded_depth_count=0)
        rows = platform_rows()+[row(START+timedelta(days=7), 19, 21, 20)]
        source = SimpleNamespace(candles=lambda *args, **kwargs: [
            Candle(datetime.combine(date.fromisoformat(r["time"]), datetime.min.time(), UTC),
                   *(Decimal(r[k]) for k in ("open", "high", "low", "close"))) for r in rows])
        count, errors = update_official(next_day, client=source)
        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        observation = self.watch.observations.get(as_of_date=next_day)
        self.assertFalse(observation.result["ranked"])
        self.assertEqual(observation.result["dropout_days"], 1)
        response = self.client.get(reverse("daily_mover_monitor:detail", args=["TESTUSDT"]), {"date": next_day.isoformat()})
        self.assertContains(response, "已掉榜 1 天")
        self.assertContains(response, "可复核依据与事件")
        self.assertContains(self.client.get(reverse("daily_mover_monitor:watchlist")), "TESTUSDT")

    def test_ending_followup_retains_observations(self):
        end = self.day+timedelta(days=30)
        rows = platform_rows()+[row(START+timedelta(days=i), 19, 21, 20) for i in range(7, 37)]
        observe(self.watch, end, rows)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.ended_on, end)
        self.assertEqual(self.watch.observations.count(), 1)

    def test_official_update_without_board_does_not_invent_dropout(self):
        count, errors = update_official(self.day)
        self.assertEqual(count, 0)
        self.assertTrue(errors)
        self.assertEqual(self.watch.observations.count(), 0)

    def test_partial_collection_failure_schedules_retry(self):
        now = datetime.combine(self.day, datetime.min.time(), UTC)+timedelta(hours=1)
        schedule = DailyMoverSchedule.objects.create(next_run_at=now+timedelta(days=1))
        with patch("apps.daily_mover_monitor.structure_service.update_official", return_value=(0, ["采集失败"])), patch("apps.daily_mover_monitor.service.timezone.now", return_value=now):
            _update_structures(self.day, None)
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, now+timedelta(minutes=5))

    def test_incomplete_input_can_be_retried_without_publishing_bad_snapshot(self):
        with self.assertRaises(ValueError):
            observe(self.watch, self.day, [])
        self.assertEqual(self.watch.observations.count(), 0)
        self.assertEqual(self.client.get(reverse("daily_mover_monitor:detail", args=[self.watch.symbol]), {"date": self.day.isoformat()}).status_code, 200)
        self.assertEqual(observe(self.watch, self.day, platform_rows()).result["state"], "平台暂时成立")

    def test_failure_followup_ends_after_ten_days(self):
        rows = platform_rows()+[row(START+timedelta(days=i), 17, 20, 18) for i in range(7, 18)]
        end = START+timedelta(days=18)
        observe(self.watch, end, rows)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.ended_on, end)
        self.assertIn("失效", self.watch.end_reason)

    def test_replay_is_separate_and_rerun_keeps_one_cycle(self):
        entry = SimpleNamespace(symbol="TESTUSDT", quote_asset="USDT", low_at=datetime.combine(START, datetime.min.time(), UTC), low_price=10, candles=platform_rows(), snapshot=SimpleNamespace(as_of_date=self.day))
        by_day = {self.day: [entry], self.day+timedelta(days=1): []}
        rows = platform_rows()+[row(START+timedelta(days=7), 19, 21, 20)]
        source = SimpleNamespace(candles=lambda *args, **kwargs: [
            Candle(datetime.combine(date.fromisoformat(r["time"]), datetime.min.time(), UTC),
                   *(Decimal(r[k]) for k in ("open", "high", "low", "close"))) for r in rows])
        with patch("apps.daily_mover_monitor.structure_service.preview_rows", return_value=(by_day, None)):
            self.assertEqual(replay(client=source)[1], [])
            hashes = list(StructureObservation.objects.values_list("input_hash", flat=True))
            self.assertEqual(replay(client=source)[1], [])
        self.assertEqual(StructureWatch.objects.filter(origin="replay").count(), 1)
        self.assertEqual(list(StructureObservation.objects.values_list("input_hash", flat=True)), hashes)
        self.assertEqual(self.watch.observations.count(), 0)
        official_page = self.client.get(reverse("daily_mover_monitor:watchlist"))
        replay_page = self.client.get(reverse("daily_mover_monitor:watchlist"), {"origin": "replay"})
        self.assertContains(replay_page, "历史回放")
        self.assertNotContains(official_page, "平台暂时成立")
