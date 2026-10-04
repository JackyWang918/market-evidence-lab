"""Download and verify Binance's public daily USD-M kline archives."""

import csv
import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


FIELDS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base_volume",
    "taker_buy_quote_volume",
]
ARCHIVE_BASE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/5m"


def _download_day(day):
    name = f"BTCUSDT-5m-{day.isoformat()}.zip"
    url = f"{ARCHIVE_BASE}/{name}"
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=True) as client:
        archive = client.get(url)
        archive.raise_for_status()
        checksum = client.get(url + ".CHECKSUM")
        checksum.raise_for_status()
    expected_hash = checksum.text.split()[0].lower()
    actual_hash = hashlib.sha256(archive.content).hexdigest()
    if expected_hash != actual_hash:
        raise ValueError(f"Checksum mismatch for {name}")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        with zipped.open(name.replace(".zip", ".csv")) as raw:
            rows = list(csv.reader(io.TextIOWrapper(raw, encoding="utf-8")))
    if rows and rows[0][0] == "open_time":
        rows.pop(0)
    return rows


def download_latest_complete_days(count: int, output: Path, now: datetime) -> tuple[datetime, datetime]:
    """Save exactly count 5m bars ending at the previous UTC midnight."""
    end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(minutes=5 * count)
    days = []
    day = start.date()
    while day < end.date():
        days.append(day)
        day += timedelta(days=1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(_download_day, days))
    rows = [row for page in pages for row in page]
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    selected = [row for row in rows if start_ms <= int(row[0]) < end_ms]
    if len(selected) != count:
        raise ValueError(f"Expected {count} bars, got {len(selected)}")
    for index, row in enumerate(selected):
        opening = start_ms + index * 300_000
        if len(row) < 11 or int(row[0]) != opening or int(row[6]) != opening + 299_999:
            raise ValueError(f"Missing or malformed candle at offset {index}")
        low, high = float(row[3]), float(row[2])
        if not (low <= float(row[1]) <= high and low <= float(row[4]) <= high):
            raise ValueError(f"Invalid OHLC at offset {index}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(FIELDS)
        writer.writerows(row[:11] for row in selected)
    temporary.replace(output)
    return start, end
