import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Max, Min
from django.http import Http404
from django.shortcuts import render

from .experiment import candle_change_pct, candle_direction, read_candles
from .live import load_state
from .models import BtcFiveMinuteCandle


def _experiment_files():
    directory = Path(settings.BASE_DIR) / "data" / "btc_prediction" / "experiments"
    files = list(directory.glob("continuity_v1_*.json"))
    if not files:
        return None, None
    summary_path = max(files, key=lambda path: path.stat().st_mtime)
    return summary_path, summary_path.with_suffix(".csv")


def _experiment_data():
    summary_path, records_path = _experiment_files()
    if not summary_path or not records_path.is_file():
        return None, []
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    with records_path.open(newline="", encoding="utf-8") as stream:
        records = list(csv.DictReader(stream))
    for row in records:
        for key in (
            "trigger_1_open_time", "trigger_2_open_time", "trigger_3_open_time",
            "signal_open_time", "prediction_open_time",
        ):
            row[key] = datetime.fromtimestamp(int(row[key]) / 1000, UTC)
    return summary, records


def _live_data():
    path = Path(settings.BASE_DIR) / "data" / "btc_prediction" / "live" / "continuity_live_v1.json"
    return load_state(path)


def index(request):
    experiment, experiment_records = _experiment_data()
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
            "experiment": experiment,
            "latest_predictions": list(reversed(experiment_records[-10:])),
            "live": _live_data(),
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
            "experiment": experiment,
            "latest_predictions": list(reversed(experiment_records[-10:])),
            "live": _live_data(),
        },
    )


def predictions(request):
    summary, records = _experiment_data()
    if summary is None:
        raise Http404("尚未运行连续性实验")
    outcome = request.GET.get("outcome", "all")
    if outcome not in {"all", "win", "loss", "pending"}:
        raise Http404("未知结果筛选")
    if outcome != "all":
        records = [row for row in records if row["outcome"] == outcome]
    page = Paginator(list(reversed(records)), 50).get_page(request.GET.get("page"))
    return render(request, "btc_prediction/predictions.html", {
        "summary": summary, "page": page, "outcome": outcome,
    })


def prediction_detail(request, sequence: int):
    summary, records = _experiment_data()
    if summary is None or sequence < 1 or sequence > len(records):
        raise Http404("预测记录不存在")
    record = records[sequence - 1]
    if int(record["sequence"]) != sequence:
        raise Http404("预测序号不一致")
    data_dir = Path(settings.BASE_DIR) / "data" / "btc_prediction"
    source = data_dir / "experiments" / summary["source_file"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != summary["source_sha256"]:
        raise Http404("实验输入快照校验失败")
    candles = read_candles(source)
    wanted = {
        record["trigger_1_open_time"], record["trigger_2_open_time"],
        record["trigger_3_open_time"], record["signal_open_time"],
        record["prediction_open_time"],
    }
    evidence = []
    for candle in candles:
        opening = datetime.fromtimestamp(int(candle["open_time"]) / 1000, UTC)
        if opening in wanted:
            evidence.append({
                **candle,
                "open_time": opening,
                "change_pct": candle_change_pct(candle),
                "direction": candle_direction(candle),
                "is_prediction": opening == record["prediction_open_time"],
                "is_signal": opening == record["signal_open_time"],
                "is_trigger": opening in {
                    record["trigger_1_open_time"], record["trigger_2_open_time"],
                    record["trigger_3_open_time"],
                },
            })
    return render(request, "btc_prediction/prediction_detail.html", {
        "summary": summary, "record": record, "evidence": evidence,
    })


def live_index(request):
    state = _live_data()
    if state is None:
        raise Http404("实时实验尚未启动")
    records = list(reversed(state["records"]))
    page = Paginator(records, 50).get_page(request.GET.get("page"))
    stats = {
        "wins": sum(row["outcome"] == "win" for row in records),
        "losses": sum(row["outcome"] == "loss" for row in records),
        "pending": sum(row["outcome"] == "pending" for row in records),
    }
    for row in page:
        row["prediction_open_datetime"] = datetime.fromtimestamp(
            row["prediction_open_time"] / 1000, UTC
        )
    return render(request, "btc_prediction/live.html", {
        "state": state, "page": page, "stats": stats,
    })


def live_detail(request, sequence: int):
    state = _live_data()
    if state is None or sequence < 1 or sequence > len(state["records"]):
        raise Http404("实时预测记录不存在")
    record = state["records"][sequence - 1]
    evidence = []
    seen = set()
    for role, candle in (
        [("三连 K", item) for item in record["trigger_candles"]]
        + [("预测来源 K", record["signal_candle"])]
        + ([("被预测 K", record["actual_candle"])] if record["actual_candle"] else [])
    ):
        opening = int(candle["open_time"])
        if opening in seen:
            continue
        seen.add(opening)
        evidence.append({
            **candle, "role": role,
            "open_datetime": datetime.fromtimestamp(opening / 1000, UTC),
            "change_pct": candle_change_pct(candle),
            "direction": candle_direction(candle),
        })
    return render(request, "btc_prediction/live_detail.html", {
        "record": record, "evidence": evidence,
        "prediction_open_datetime": datetime.fromtimestamp(
            record["prediction_open_time"] / 1000, UTC
        ),
    })
