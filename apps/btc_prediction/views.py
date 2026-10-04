import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Max, Min
from django.shortcuts import render

from .models import BtcFiveMinuteCandle


def index(request):
    data_dir = Path(settings.BASE_DIR) / "data" / "btc_prediction"
    files = list(data_dir.glob("btcusdt_5m_*.csv"))
    if files:
        data_file = max(files, key=lambda path: path.stat().st_mtime)
        with data_file.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["open_time"] = datetime.fromtimestamp(int(row["open_time"]) / 1000, UTC)
        recent = list(reversed(rows[-24:]))
        latest_contiguous = 0
        previous = None
        for row in recent:
            if previous and previous - row["open_time"] != timedelta(minutes=5):
                break
            latest_contiguous += 1
            previous = row["open_time"]
        return render(request, "btc_prediction/index.html", {
            "count": len(rows),
            "first": rows[0]["open_time"] if rows else None,
            "last": rows[-1]["open_time"] if rows else None,
            "recent": recent,
            "latest_contiguous": latest_contiguous,
            "source": "官方历史档案 CSV（SHA-256 校验）",
        })

    candles = BtcFiveMinuteCandle.objects.all()
    try:
        summary = candles.aggregate(first=Min("open_time"), last=Max("open_time"))
        count = candles.count()
        recent = list(candles.order_by("-open_time")[:24])
    except DatabaseError:
        summary, count, recent = {"first": None, "last": None}, 0, []
    latest_contiguous = 0
    previous = None
    for candle in recent:
        if previous and previous.open_time - candle.open_time != timedelta(minutes=5):
            break
        latest_contiguous += 1
        previous = candle
    return render(
        request,
        "btc_prediction/index.html",
        {
            "count": count,
            "first": summary["first"],
            "last": summary["last"],
            "recent": recent,
            "latest_contiguous": latest_contiguous,
            "source": "数据库",
        },
    )
