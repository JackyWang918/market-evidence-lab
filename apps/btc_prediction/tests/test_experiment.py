from django.test import SimpleTestCase

from apps.btc_prediction.experiment import candle_direction, replay


def candle(index: int, close: str) -> dict[str, str]:
    opening = index * 300_000
    return {
        "open_time": str(opening),
        "close_time": str(opening + 299_999),
        "open": "100.00",
        "high": "101.00",
        "low": "99.00",
        "close": close,
    }


class ContinuityReplayTests(SimpleTestCase):
    def test_flat_bridges_streak_and_win_continues_until_loss(self):
        candles = [
            candle(0, "101"), candle(1, "100"), candle(2, "101"),
            candle(3, "101"), candle(4, "99"),
            candle(5, "99"), candle(6, "100"), candle(7, "99"),
            candle(8, "100"), candle(9, "101"),
        ]
        rows = replay(candles)
        self.assertEqual([r["outcome"] for r in rows], ["win", "loss", "win", "loss"])
        self.assertEqual([r["direction"] for r in rows], ["up", "up", "down", "down"])
        self.assertEqual([r["phase"] for r in rows], [
            "three_candles", "continuation", "three_candles", "continuation"
        ])
        self.assertEqual([r["chain_id"] for r in rows], ["1", "1", "2", "2"])
        self.assertEqual([r["prediction_open_time"] for r in rows], [
            str(i * 300_000) for i in (3, 4, 8, 9)
        ])
        self.assertEqual(rows[2]["trigger_2_open_time"], str(6 * 300_000))

    def test_displayed_zero_percent_extends_existing_direction_but_cannot_start_it(self):
        candles = [
            candle(0, "100"), candle(1, "101"), candle(2, "100.004"),
            candle(3, "101"), candle(4, "99"),
        ]
        self.assertEqual(candle_direction(candles[2]), "flat")
        self.assertEqual(candle_direction(candle(2, "99.996")), "flat")
        rows = replay(candles)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prediction_open_time"], str(4 * 300_000))
        self.assertEqual(rows[0]["outcome"], "loss")

    def test_last_signal_remains_pending(self):
        rows = replay([candle(i, "101") for i in range(4)])
        self.assertEqual([r["outcome"] for r in rows], ["win", "pending"])
        self.assertEqual(rows[-1]["prediction_open_time"], str(4 * 300_000))
