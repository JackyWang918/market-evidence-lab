from django.db import models


class BtcFiveMinuteCandle(models.Model):
    """Closed BTCUSDT USD-M futures candles, keyed by UTC open time."""

    open_time = models.DateTimeField(unique=True)
    close_time = models.DateTimeField()
    open = models.DecimalField(max_digits=40, decimal_places=18)
    high = models.DecimalField(max_digits=40, decimal_places=18)
    low = models.DecimalField(max_digits=40, decimal_places=18)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    volume = models.DecimalField(max_digits=40, decimal_places=18)
    quote_volume = models.DecimalField(max_digits=40, decimal_places=18)
    trade_count = models.PositiveBigIntegerField()
    taker_buy_base_volume = models.DecimalField(max_digits=40, decimal_places=18)
    taker_buy_quote_volume = models.DecimalField(max_digits=40, decimal_places=18)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["open_time"]

    def __str__(self) -> str:
        return f"BTCUSDT 5m {self.open_time.isoformat()}"
