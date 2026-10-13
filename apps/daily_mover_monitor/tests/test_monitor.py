from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.daily_mover_monitor.detector import Candle, detect, window_signal
from apps.daily_mover_monitor.models import DailyMoverEntry, DailyMoverSnapshot
from apps.daily_mover_monitor.preview import import_preview
from apps.daily_mover_monitor.service import scan
from apps.daily_mover_monitor.source import FuturesMarketClient
from apps.market_data.binance import BinanceKlineClient


DAY = date(2026, 10, 12)
SCAN_TIME = datetime(2026, 10, 12, 0, 21, tzinfo=UTC)


def candles(symbol="five"):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    if symbol == "five":
        closes = [10, 10, 10, 10, 10, 10, 10, 10, 20, 20, 20]
        highs = [11, 11, 11, 11, 11, 11, 11, 11, 22, 21, 21]
        lows = [10, 10, 10, 10, 10, 10, 9, 10, 18, 19, 19]
    elif symbol == "seven":
        closes = [5, 5, 5, 5, 5, 9, 9, 9, 9, 9, 9]
        highs = [6, 6, 6, 6, 6, 10, 10, 10, 10, 10, 10]
        lows = [5, 5, 5, 5, 5, 9, 9, 9, 9, 9, 9]
    else:
        closes = [10] * 11
        highs = [11] * 11
        lows = [9] * 11
    return [
        Candle(start + timedelta(days=index), Decimal(closes[index]),
               Decimal(highs[index]), Decimal(lows[index]), Decimal(closes[index]))
        for index in range(11)
    ]


class DetectorTests(SimpleTestCase):
    def test_both_windows_and_seven_only(self):
        self.assertEqual(detect(candles("five"))[0], "5D+7D")
        self.assertEqual(detect(candles("seven"))[0], "7D 涨80%+")

    def test_rejects_high_before_low_even_if_range_doubles(self):
        rows = candles("five")
        rows[6] = Candle(rows[6].open_at, Decimal(10), Decimal(25), Decimal(10), Decimal(10))
        rows[8] = Candle(rows[8].open_at, Decimal(20), Decimal(21), Decimal(8), Decimal(20))
        self.assertIsNone(detect(rows))

    def test_rejects_missing_daily_candle(self):
        rows = candles("five")
        rows[3] = Candle(rows[3].open_at + timedelta(days=1), *(
            rows[3].open, rows[3].high, rows[3].low, rows[3].close
        ))
        self.assertIsNone(detect(rows))

    def test_ten_day_change_accepts_20_percent_boundary(self):
        rows = candles("five")
        rows[-1] = Candle(rows[-1].open_at, Decimal(20), Decimal(21), Decimal(12), Decimal(12))
        self.assertEqual(detect(rows)[2], Decimal(20))
        rows[-1] = Candle(rows[-1].open_at, Decimal(20), Decimal(21), Decimal(12), Decimal("11.99"))
        self.assertIsNone(detect(rows))

    def test_window_thresholds_accept_boundary(self):
        rows = candles("five")
        rows[8] = Candle(rows[8].open_at, Decimal(12), Decimal("13.5"), Decimal(12), Decimal(12))
        rows[9] = Candle(rows[9].open_at, Decimal(12), Decimal(13), Decimal(12), Decimal(12))
        rows[10] = Candle(rows[10].open_at, Decimal(12), Decimal(13), Decimal(12), Decimal(12))
        self.assertEqual(window_signal(rows, 5, Decimal(50)).rise_pct, Decimal(50))
        self.assertIsNone(window_signal(rows, 5, Decimal("50.01")))
        self.assertEqual(detect(rows)[0], "5D 涨50%+")
        seven = candles("seven")
        for index in range(5, 11):
            seven[index] = Candle(seven[index].open_at, Decimal(9), Decimal(9), Decimal(9), Decimal(9))
        self.assertEqual(window_signal(seven, 7, Decimal(80)).rise_pct, Decimal(80))
        self.assertIsNone(window_signal(seven, 7, Decimal("80.01")))

    def test_high_drawdown_is_reported_without_excluding_signal(self):
        rows = candles("five")
        rows[-1] = Candle(rows[-1].open_at, Decimal(20), Decimal(21), Decimal(13), Decimal(14))
        signal_name, signal, change = detect(rows)
        self.assertEqual(signal_name, "5D+7D")
        self.assertEqual(change, Decimal(40))
        self.assertGreater(signal.drawdown_pct, 30)


class SourceTests(SimpleTestCase):
    def test_detail_url_supports_non_ascii_binance_symbol(self):
        self.assertIn("%E7%89%9B", reverse("daily_mover_monitor:detail", args=["牛来USDT"]))

    def test_discovers_perpetuals_and_measures_book_without_turnover(self):
        def respond(request):
            if request.url.path.endswith("exchangeInfo"):
                return httpx.Response(200, json={"symbols": [
                    {"symbol": "ONEUSDT", "quoteAsset": "USDT", "status": "TRADING", "contractType": "PERPETUAL"},
                    {"symbol": "TWOUSDC", "quoteAsset": "USDC", "status": "TRADING", "contractType": "PERPETUAL"},
                    {"symbol": "OLDUSDT", "quoteAsset": "USDT", "status": "BREAK", "contractType": "PERPETUAL"},
                    {"symbol": "QUARTERUSDT", "quoteAsset": "USDT", "status": "TRADING", "contractType": "CURRENT_QUARTER"},
                ]})
            if request.url.path.endswith("depth"):
                return httpx.Response(200, json={
                    "bids": [["100", "100"], ["99.5", "50"], ["98", "10000"]],
                    "asks": [["100.1", "100"], ["100.5", "50"], ["102", "10000"]],
                })
            raise AssertionError(request.url.path)

        http_client = httpx.Client(transport=httpx.MockTransport(respond))
        source = FuturesMarketClient(BinanceKlineClient(http_client=http_client))
        try:
            self.assertEqual(source.symbols(), [("ONEUSDT", "USDT"), ("TWOUSDC", "USDC")])
            bid, ask, spread = source.depth("ONEUSDT")
            self.assertEqual(bid, Decimal("14975.0"))
            self.assertEqual(ask, Decimal("15035.0"))
            self.assertLess(spread, Decimal(11))
        finally:
            http_client.close()

    def test_fetches_eleven_closed_daily_candles(self):
        def respond(request):
            self.assertEqual(request.url.params["limit"], "11")
            self.assertEqual(request.url.params["startTime"], str(int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)))
            return httpx.Response(200, json=[])

        http_client = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            source = FuturesMarketClient(BinanceKlineClient(http_client=http_client))
            self.assertEqual(source.candles("FIVEUSDT", DAY), [])
        finally:
            http_client.close()


class FakeSource:
    def __init__(self, *, thin=False):
        self.thin = thin
        self.calls = []

    def symbols(self):
        return [("FIVEUSDT", "USDT"), ("SEVENUSDT", "USDT"), ("FLATUSDT", "USDT")]

    def candles(self, symbol, day, *, days=11):
        self.calls.append(("candles", symbol))
        return candles({"FIVEUSDT": "five", "SEVENUSDT": "seven"}.get(symbol, "flat"))

    def depth(self, symbol):
        self.calls.append(("depth", symbol))
        amount = Decimal(100) if self.thin and symbol == "SEVENUSDT" else Decimal(20000)
        return amount, amount, Decimal(10)


class SnapshotTests(TestCase):
    def test_scan_saves_ranked_snapshot_and_reuses_it(self):
        source = FakeSource(thin=True)
        snapshot = scan(DAY, client=source, now=SCAN_TIME)
        self.assertEqual(snapshot.scanned_count, 3)
        self.assertEqual(snapshot.candidate_count, 2)
        self.assertEqual(snapshot.excluded_depth_count, 1)
        self.assertEqual(list(snapshot.entries.values_list("symbol", flat=True)), ["FIVEUSDT"])
        self.assertEqual(source.calls.count(("depth", "FLATUSDT")), 0)
        self.assertEqual(scan(DAY, client=source, now=SCAN_TIME).pk, snapshot.pk)
        self.assertEqual(DailyMoverSnapshot.objects.count(), 1)
        self.assertEqual(snapshot.rule_version, "daily-mover-v2")
        self.assertEqual(DailyMoverEntry.objects.get().consecutive_days, 1)
        with self.assertRaises(RuntimeError):
            snapshot.delete()
        with self.assertRaises(RuntimeError):
            DailyMoverEntry.objects.filter(snapshot=snapshot).update(rank=2)

        board = self.client.get(reverse("daily_mover_monitor:index"), {"date": DAY.isoformat()})
        self.assertContains(board, "FIVEUSDT")
        self.assertContains(board, "较 10 天前")
        self.assertContains(board, "前日无数据")
        self.assertContains(board, 'class="dm-mobile-list"')
        self.assertContains(board, 'class="dm-tab-short"')
        detail = self.client.get(reverse("daily_mover_monitor:detail", args=["FIVEUSDT"]), {"date": DAY.isoformat()})
        self.assertContains(detail, "日 K 与上涨区间")
        history = self.client.get(reverse("daily_mover_monitor:history"))
        self.assertContains(history, "历史日期")

    @patch("apps.daily_mover_monitor.views.today_in_shanghai", return_value=DAY)
    def test_dashboard_has_empty_state(self, _):
        response = self.client.get(reverse("daily_mover_monitor:index"))
        self.assertContains(response, "该日尚无榜单快照")
        self.assertEqual(response.context["selected_day"], DAY - timedelta(days=1))
        self.assertEqual(
            response.context["recent_days"],
            [DAY - timedelta(days=index) for index in range(8, 0, -1)],
        )

    @patch("apps.daily_mover_monitor.views.today_in_shanghai", return_value=DAY)
    def test_homepage_shows_latest_completed_scan_and_preserves_explicit_history(self, _):
        DailyMoverSnapshot.objects.create(
            as_of_date=DAY,
            candle_open_at=datetime(2026, 10, 11, tzinfo=UTC),
            scanned_count=1,
            candidate_count=0,
            excluded_depth_count=0,
        )
        response = self.client.get(reverse("daily_mover_monitor:index"))
        self.assertEqual(response.context["selected_day"], DAY)
        self.assertIn(DAY, response.context["recent_days"])
        self.assertEqual(response.context["price_day"], DAY-timedelta(days=1))
        historical = self.client.get(reverse("daily_mover_monitor:index"), {"date": (DAY-timedelta(days=1)).isoformat()})
        self.assertEqual(historical.context["selected_day"], DAY-timedelta(days=1))

    def test_scan_rejects_unclosed_day(self):
        with self.assertRaisesRegex(ValueError, "08:20"):
            scan(DAY, client=FakeSource(), now=datetime(2026, 10, 12, 0, 10, tzinfo=UTC))

    @patch("apps.daily_mover_monitor.views.today_in_shanghai", return_value=DAY)
    def test_historical_preview_is_visible_without_official_snapshots(self, _):
        row = {
            "symbol": "FIVEUSDT", "quote_asset": "USDT", "signal": "5D+7D",
            "close": "20", "rise_pct": "120", "drawdown_pct": "40",
            "change_8d_pct": "80", "low_at": "2026-10-04", "low_price": "9",
            "high_at": "2026-10-09", "high_price": "22", "preview_rank": 1,
            "current_bid_depth": "20000", "current_ask_depth": "22000",
            "current_spread_bps": "5", "current_depth_pass": True,
        }
        with TemporaryDirectory() as directory:
            for day in ("2026-10-10", "2026-10-11"):
                payload = {
                    "symbol_count": 563,
                    "scan_completed_at_utc": "2026-10-11T17:27:00+00:00",
                    "price_candidates": {day: [row]},
                    "provisional_rankings": {day: [row]},
                    "candles": {day: {"FIVEUSDT": [{"time": "2026-10-10", "open": "19", "high": "22", "low": "18", "close": "20"}]}},
                }
                path = Path(directory) / f"{day}.json"
                path.write_text(json.dumps(payload))
                import_preview(path)

        board = self.client.get(reverse("daily_mover_monitor:index"))
        self.assertEqual(board.context["selected_day"], date(2026, 10, 11))
        self.assertContains(board, "FIVEUSDT")
        self.assertContains(board, "历史试算 · 非正式快照")
        self.assertContains(board, "盘口取自 2026-10-12 01:27 北京时间")
        self.assertContains(board, "至少连榜 2 天")
        self.assertContains(board, "高回撤")
        self.assertContains(board, "较 8 天前")
        self.assertEqual(board.context["entries"][0].consecutive_days, 2)
        history = self.client.get(reverse("daily_mover_monitor:history"), {"date": "2026-10-10"})
        self.assertContains(history, "FIVEUSDT")
        self.assertContains(history, "前日无数据")
        detail = self.client.get(reverse("daily_mover_monitor:detail", args=["FIVEUSDT"]), {"date": "2026-10-11"})
        self.assertContains(detail, "2026-10-10")
        self.assertContains(detail, "历史试算 · 非正式快照")
