from datetime import UTC, datetime

from django.test import SimpleTestCase

from apps.btc_prediction.live import advance_state, new_state


BASE = 1_800_000_000_000


def candle(index: int, close: str) -> dict[str, str]:
    opening = BASE + index * 300_000
    return {
        "open_time": str(opening), "close_time": str(opening + 299_999),
        "open": "100", "high": "101", "low": "99", "close": close,
    }


def time_at(index: int, seconds: int = 10) -> datetime:
    return datetime.fromtimestamp((BASE + index * 300_000) / 1000 + seconds, UTC)


class LiveContinuityTests(SimpleTestCase):
    def test_prediction_is_persisted_before_result_and_winner_continues(self):
        state = new_state(time_at(3), [candle(i, "101") for i in range(3)])
        self.assertEqual(state["records"][0]["outcome"], "pending")
        self.assertEqual(state["records"][0]["prediction_open_time"], BASE + 3 * 300_000)

        advance_state(state, [candle(3, "101")], time_at(4))
        self.assertEqual([r["outcome"] for r in state["records"]], ["win", "pending"])
        self.assertEqual(state["records"][1]["phase"], "continuation")
        self.assertEqual(state["records"][0]["actual_candle"]["close"], "101")

        advance_state(state, [candle(4, "99")], time_at(5))
        self.assertEqual([r["outcome"] for r in state["records"]], ["win", "loss"])
        self.assertIsNone(state["pending_sequence"])
        self.assertEqual(state["streak_candles"], [])

    def test_late_start_does_not_backdate_a_prediction(self):
        state = new_state(time_at(3, seconds=90), [candle(i, "101") for i in range(3)])
        self.assertEqual(state["records"], [])
        self.assertEqual(len(state["streak_candles"]), 3)

    def test_missing_history_marks_pending_prediction_unresolved(self):
        state = new_state(time_at(3), [candle(i, "101") for i in range(3)])
        advance_state(state, [candle(5, "99"), candle(6, "99")], time_at(7))
        self.assertEqual(state["records"][0]["outcome"], "unresolved_gap")
        self.assertEqual(state["data_gaps"], 1)
        self.assertIsNone(state["pending_sequence"])
