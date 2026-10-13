from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from django.test import TestCase, override_settings

from apps.daily_mover_monitor.models import (
    DailyMoverDigest, DailyMoverEntry, DailyMoverSnapshot,
    StructureObservation, StructureWatch,
)
from apps.daily_mover_monitor.report import build_digest, send_digest


FIRST = date(2026, 10, 12)
SECOND = FIRST + timedelta(days=1)


def snapshot(day):
    return DailyMoverSnapshot.objects.create(
        as_of_date=day,
        candle_open_at=datetime.combine(day - timedelta(days=1), datetime.min.time(), UTC),
        scanned_count=563,
        candidate_count=17,
        excluded_depth_count=0,
        rule_version="daily-mover-v2",
    )


def entry(snap, symbol, rank, drawdown):
    return DailyMoverEntry.objects.create(
        snapshot=snap, rank=rank, symbol=symbol, quote_asset="USDT",
        signal="5D 涨50%+", close=Decimal("12"),
        baseline_change_pct=Decimal("30"), rise_pct=Decimal("67.1"),
        drawdown_pct=Decimal(drawdown), low_price=Decimal("10"),
        high_price=Decimal("15"), low_at=snap.candle_open_at,
        high_at=snap.candle_open_at, bid_depth=Decimal("20000"),
        ask_depth=Decimal("20000"), spread_bps=Decimal("5"),
        consecutive_days=1, previous_rank=None, candles=[],
    )


def observe(symbol, day, status):
    watch, _ = StructureWatch.objects.get_or_create(
        symbol=symbol, origin="official", discovered_on=FIRST,
        defaults={"quote_asset": "USDT", "last_ranked_on": day,
                  "anchor_date": FIRST - timedelta(days=5), "anchor_price": Decimal("10")},
    )
    return StructureObservation.objects.create(
        watch=watch, as_of_date=day, rule_version="structure-trial-v1.1",
        data_cutoff_at=datetime.combine(day - timedelta(days=1), datetime.min.time(), UTC),
        input_hash="a" * 64,
        result={"continuation": {"plans": [
            {"direction": "long", "status": status},
            {"direction": "short", "status": "等待向下跌破"},
        ]}},
    )


class DigestTests(TestCase):
    def setUp(self):
        first, second = snapshot(FIRST), snapshot(SECOND)
        entry(first, "GTCUSDT", 12, "10")
        entry(first, "STRKUSDT", 1, "20")
        entry(second, "GTCUSDT", 15, "13.8")
        entry(second, "STRKUSDT", 11, "32.9")
        entry(second, "AINUSDT", 1, "5.6")
        observe("GTCUSDT", FIRST, "等待向上突破")
        observe("GTCUSDT", SECOND, "突破待站稳")
        observe("STRKUSDT", FIRST, "等待向上突破")
        observe("STRKUSDT", SECOND, "等待向上突破")
        observe("AINUSDT", SECOND, "等待向上突破")
        self.current = second

    def test_digest_uses_changes_and_official_data_only(self):
        content = build_digest(self.current)
        self.assertIn("GTCUSDT", content)
        self.assertIn("GTCUSDT：", content)
        self.assertNotIn("https://www.binance.com", content)
        self.assertIn("向上突破待站稳", content)
        self.assertIn("STRKUSDT", content)
        self.assertIn("#1→#11", content)
        self.assertIn("AINUSDT", content)
        self.assertIn("新入榜第 1", content)
        self.assertNotIn("webhook", content)

    @override_settings(DAILY_MOVER_WECHAT_WEBHOOK_URL="https://example.test/webhook")
    def test_success_is_sent_once_even_if_task_retries(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            first = send_digest(self.current, client=client)
            second = send_digest(self.current, client=client)
        self.assertEqual(first.status, DailyMoverDigest.Status.SENT)
        self.assertEqual(second.status, DailyMoverDigest.Status.SENT)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(requests), 1)
        self.assertIn("GTCUSDT", requests[0].read().decode())
        self.assertEqual(DailyMoverDigest.objects.get().attempts, 1)

    @override_settings(DAILY_MOVER_WECHAT_WEBHOOK_URL="https://example.test/webhook")
    def test_unknown_delivery_is_not_retried_automatically(self):
        requests = []

        def timeout(request):
            requests.append(request)
            raise httpx.ReadTimeout("timeout")

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            first = send_digest(self.current, client=client)
            second = send_digest(self.current, client=client)
        self.assertEqual(first.status, DailyMoverDigest.Status.UNKNOWN)
        self.assertEqual(second.attempts, 1)
        self.assertEqual(len(requests), 1)

    @override_settings(DAILY_MOVER_WECHAT_WEBHOOK_URL="https://example.test/webhook")
    def test_explicit_rejection_can_retry_without_rebuilding_report(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"errcode": 93000 if len(calls) == 1 else 0})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            first = send_digest(self.current, client=client)
            second = send_digest(self.current, client=client)
        self.assertEqual(first.status, DailyMoverDigest.Status.REJECTED)
        self.assertEqual(second.status, DailyMoverDigest.Status.SENT)
        self.assertEqual(second.attempts, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].read(), calls[1].read())

    def test_incomplete_structure_prevents_report(self):
        entry(self.current, "MISSINGUSDT", 18, "3")
        with self.assertRaisesRegex(ValueError, "结构观察尚未完成"):
            build_digest(self.current)
