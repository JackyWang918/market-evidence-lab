from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep

from django.conf import settings

from apps.market_data.binance import BinanceClientError, BinanceKlineClient

from .detector import Candle


class FuturesMarketClient:
    def __init__(self, kline_client: BinanceKlineClient | None = None):
        self.client = kline_client or BinanceKlineClient()
        self.owns_client = kline_client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def _get(self, path: str, *, params: dict | None = None):
        for attempt in range(4):
            try:
                response = self.client.http_client.get(
                    f"{self.client.base_url}{path}", params=params
                )
            except Exception as exc:
                if attempt == 3:
                    raise BinanceClientError(f"Binance request failed: {type(exc).__name__}") from exc
                sleep(0.5 * (2**attempt))
                continue
            if response.status_code in (418, 429) or response.status_code >= 500:
                if attempt == 3:
                    raise BinanceClientError(f"Binance request failed: HTTP {response.status_code}")
                sleep(min(8, 0.5 * (2**attempt)))
                continue
            if response.status_code >= 400:
                raise BinanceClientError(f"Binance request failed: HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise BinanceClientError("Binance returned invalid JSON") from exc
        raise BinanceClientError("Binance request failed")

    def symbols(self) -> list[tuple[str, str]]:
        payload = self._get("/fapi/v1/exchangeInfo")
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise BinanceClientError("Binance exchange information is invalid")
        quotes = set(settings.DAILY_MOVER_QUOTE_ASSETS)
        symbols = [
            (str(row["symbol"]), str(row["quoteAsset"]))
            for row in payload["symbols"]
            if isinstance(row, dict)
            and row.get("status") == "TRADING"
            and row.get("contractType") == "PERPETUAL"
            and row.get("quoteAsset") in quotes
            and isinstance(row.get("symbol"), str)
        ]
        return sorted(set(symbols))

    def candles(self, symbol: str, as_of_date: date) -> list[Candle]:
        end = datetime.combine(as_of_date, datetime.min.time(), UTC)
        start = end - timedelta(days=9)
        rows = self._get(
            "/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": "1d",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000) - 1,
                "limit": 9,
            },
        )
        if not isinstance(rows, list):
            raise BinanceClientError("Binance kline response is invalid")
        parsed = [self.client._parse_row(row) for row in rows]
        return [
            Candle(row.open_time, row.open, row.high, row.low, row.close)
            for row in parsed
            if start <= row.open_time < end and row.close_time < end
        ]

    def depth(self, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
        payload = self._get("/fapi/v1/depth", params={"symbol": symbol, "limit": 1000})
        try:
            bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in payload["bids"]]
            asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in payload["asks"]]
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            if best_bid <= 0 or best_ask <= best_bid:
                raise ValueError("invalid best prices")
            mid = (best_bid + best_ask) / 2
            bid_depth = sum((p * q for p, q in bids if p >= mid * Decimal("0.99")), Decimal(0))
            ask_depth = sum((p * q for p, q in asks if p <= mid * Decimal("1.01")), Decimal(0))
            return bid_depth, ask_depth, (best_ask - best_bid) / mid * 10000
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
            raise BinanceClientError("Binance depth response is invalid") from exc
