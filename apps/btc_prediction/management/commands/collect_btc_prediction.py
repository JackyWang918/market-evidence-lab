from datetime import UTC, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.market_data.binance import BinanceClientError, BinanceKlineClient

from ...models import BtcFiveMinuteCandle


FIELDS = (
    "close_time", "open", "high", "low", "close", "volume", "quote_volume",
    "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
)


class Command(BaseCommand):
    help = "Fetch the latest N closed BTCUSDT USD-M futures 5m candles."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10_000)

    def handle(self, *args, **options):
        count = options["count"]
        if count < 2 or count > 100_000:
            raise CommandError("--count must be between 2 and 100000")

        now = timezone.now().astimezone(UTC)
        end = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        start = end - timedelta(minutes=5 * count)
        client = BinanceKlineClient()
        try:
            payloads = [
                row
                for batch in client.iter_batches(
                    symbol="BTCUSDT", interval="5m", range_start=start, range_end=end
                )
                for row in batch
            ]
        except BinanceClientError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            client.close()

        if len(payloads) != count:
            raise CommandError(f"Expected {count} closed candles, received {len(payloads)}; nothing saved")
        for i, row in enumerate(payloads):
            expected = start + timedelta(minutes=5 * i)
            if row.open_time != expected or row.close_time != expected + timedelta(minutes=5) - timedelta(milliseconds=1):
                raise CommandError(f"Missing, duplicate or malformed candle at {expected.isoformat()}; nothing saved")
            if not (row.low <= row.open <= row.high and row.low <= row.close <= row.high):
                raise CommandError(f"Invalid OHLC at {expected.isoformat()}; nothing saved")
            if min(row.volume, row.quote_volume, row.taker_buy_base_volume, row.taker_buy_quote_volume) < 0:
                raise CommandError(f"Negative volume at {expected.isoformat()}; nothing saved")

        objects = [
            BtcFiveMinuteCandle(
                open_time=row.open_time,
                **{field: getattr(row, field) for field in FIELDS},
            )
            for row in payloads
        ]
        with transaction.atomic():
            BtcFiveMinuteCandle.objects.bulk_create(
                objects,
                batch_size=500,
                update_conflicts=True,
                unique_fields=["open_time"],
                update_fields=list(FIELDS),
            )
        self.stdout.write(self.style.SUCCESS(
            f"Saved {count} verified BTCUSDT 5m candles, {start.isoformat()} to "
            f"{payloads[-1].open_time.isoformat()} UTC; requests={client.request_count}"
        ))
