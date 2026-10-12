"""Causal daily structure replay. All prices are references, never orders."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

VERSION = "structure-trial-v1.1"
RULES = {
    "platform_days": 5, "max_width_pct": "12", "max_close_span_pct": "8",
    "confirmation_buffer_pct": "0.5", "lower_band_fraction": "0.2",
    "dropout_days": 30, "failure_followup_days": 10, "history_days": 90,
}
D = Decimal


def pack_candles(candles):
    return [{"time": c.open_at.date().isoformat(), "open": str(c.open),
             "high": str(c.high), "low": str(c.low), "close": str(c.close)} for c in candles]


def space(entry, invalid, pressure):
    if entry is None or invalid is None:
        return {"r": None, "reason": "等待有效入场和失效参考价"}
    entry, invalid = D(str(entry)), D(str(invalid))
    if entry <= invalid or invalid <= 0:
        return {"r": None, "reason": "入场参考价须高于有效失效价"}
    if pressure is None:
        return {"r": None, "reason": "未识别上方压力"}
    pressure = D(str(pressure))
    if pressure <= entry:
        return {"r": None, "reason": "已到达或越过压力参考价"}
    return {"r": str((pressure-entry)/(entry-invalid)), "reason": "",
            "upside_pct": str((pressure/entry-1)*100),
            "risk_pct": str((1-invalid/entry)*100)}


def unavailable(reason, cutoff, rows=()):
    return {"version": VERSION, "rules": RULES, "state": "数据不足", "reason": reason,
            "cutoff": cutoff.isoformat(), "platform": None, "candles": list(rows),
            "plans": [{"name": name, "status": "无法判断", "next": reason,
                       "entry": None, "invalid": None, "pressure": None,
                       "space": {"r": None, "reason": reason}} for name in ("回撤确认", "平台突破")],
            "events": [], "checks": []}


def analyze(rows, *, cutoff, anchor_date, anchor_price):
    """Replay a fixed cycle through cutoff; suffix data never participates."""
    rows = [r for r in rows if r["time"] <= cutoff.isoformat()]
    rows = sorted(rows, key=lambda r: r["time"])
    if not rows or rows[-1]["time"] != cutoff.isoformat():
        return unavailable("缺少截止日完整日 K，等待补采", cutoff, rows)
    try:
        parsed = [{"time": date.fromisoformat(r["time"]),
                   **{k: D(str(r[k])) for k in ("open", "high", "low", "close")}} for r in rows]
        for i, r in enumerate(parsed):
            if any(not r[k].is_finite() or r[k] <= 0 for k in ("open", "high", "low", "close")):
                raise ValueError("价格不是有效正数")
            if r["low"] > min(r["open"], r["close"]) or r["high"] < max(r["open"], r["close"]) or r["low"] > r["high"]:
                raise ValueError("OHLC 不一致")
            if i and r["time"] != parsed[i-1]["time"] + timedelta(days=1):
                raise ValueError("日 K 不连续")
        baseline = D(str(anchor_price))
        if not baseline.is_finite() or baseline <= 0 or parsed[0]["time"] > anchor_date:
            raise ValueError("缺少本轮启动锚点数据")
    except (InvalidOperation, ValueError, KeyError, TypeError) as exc:
        return unavailable(str(exc), cutoff, rows)
    cycle = [r for r in parsed if r["time"] >= anchor_date]
    peak = cycle[0]
    platform = None
    events = []
    failure = None
    touch = None
    touch_low = None
    rebound = None
    pull = None
    breakout = None
    retest = None
    pull_failed = None
    stop = False
    buffer = D("1.005")
    floor_buffer = D("0.995")
    for i, bar in enumerate(cycle):
        if platform is None:
            if bar["high"] > peak["high"]:
                peak = bar
            consolidation = [r for r in cycle[:i+1] if r["time"] > peak["time"]]
            recent = consolidation[-RULES["platform_days"]:]
            if len(recent) == RULES["platform_days"]:
                low = min(r["low"] for r in recent)
                high = max(r["high"] for r in recent)
                closes = [r["close"] for r in recent]
                width = (high/low-1)*100
                span = (max(closes)/min(closes)-1)*100
                if width <= D(RULES["max_width_pct"]) and span <= D(RULES["max_close_span_pct"]):
                    platform = {"low": low, "high": high, "representative": median(closes),
                                "confirmed": bar["time"], "start": recent[0]["time"],
                                "days": len(recent), "width_pct": width, "close_span_pct": span}
                    events.append({"date": bar["time"].isoformat(), "label": "平台确认：边界冻结"})
            # A confirmation bar cannot also trigger its newly formed platform.
            continue
        if failure:
            continue
        if bar["close"] < platform["low"]*floor_buffer:
            failure = bar["time"]
            events.append({"date": failure.isoformat(), "label": "平台失效：收盘跌破下沿缓冲"})
            continue
        if pull and not pull_failed and bar["time"] > pull["date"] and bar["close"] < pull["invalid"]:
            pull_failed = bar["time"]
            events.append({"date": bar["time"].isoformat(), "label": "回撤方案失效"})
        lower_top = platform["low"] + (platform["high"]-platform["low"])*D("0.2")
        if not pull and bar["low"] <= lower_top and bar["high"] >= platform["low"]:
            if touch is None:
                touch = bar["time"]
                events.append({"date": touch.isoformat(), "label": "进入下沿观察区，仅开始观察"})
            if touch_low is None or bar["low"] < touch_low:
                touch_low = bar["low"]
                rebound = None
        # Two completed sessions without a new low; rebound pivot confirmed by a right-hand session.
        stop = bool(touch and i >= 2 and cycle[i-2]["time"] >= touch and
                    bar["low"] >= cycle[i-1]["low"] >= cycle[i-2]["low"])
        if touch and not pull and i >= 3:
            left, pivot, right = cycle[i-3:i]
            if pivot["time"] >= touch and pivot["high"] > left["high"] and pivot["high"] >= right["high"]:
                rebound = pivot["high"]
        if not pull and stop and rebound and bar["close"] > rebound*buffer:
            pull = {"date": bar["time"], "entry": bar["close"], "invalid": touch_low*floor_buffer,
                    "trigger": rebound*buffer, "pressure": _pressure(parsed, bar["time"], bar["close"], peak)}
            events.append({"date": bar["time"].isoformat(), "label": "回撤确认条件成立"})
        if breakout is None and bar["close"] > platform["high"]*buffer:
            breakout = {"date": bar["time"], "entry": bar["close"], "invalid": platform["low"]*floor_buffer,
                        "trigger": platform["high"]*buffer,
                        "pressure": _pressure(parsed, bar["time"], bar["close"], peak)}
            events.append({"date": bar["time"].isoformat(), "label": "平台收盘突破成立"})
        elif breakout and retest is None and bar["time"] > breakout["date"]:
            if platform["high"]*floor_buffer <= bar["low"] <= platform["high"]*buffer and bar["close"] > platform["high"]:
                retest = bar["time"]
                events.append({"date": bar["time"].isoformat(), "label": "突破后回踩确认"})
    last = cycle[-1]
    since_peak = (cutoff-peak["time"]).days
    state = "上涨延续" if since_peak == 0 else "回撤中"
    consolidation_days = 0
    post_peak = [r for r in cycle if r["time"] > peak["time"]]
    for length in range(1, len(post_peak)+1):
        window = post_peak[-length:]
        width = (max(r["high"] for r in window)/min(r["low"] for r in window)-1)*100
        span = (max(r["close"] for r in window)/min(r["close"] for r in window)-1)*100
        if width > D(RULES["max_width_pct"]) or span > D(RULES["max_close_span_pct"]):
            break
        consolidation_days = length
    if not platform and consolidation_days >= 2:
        state = "平台形成中"
    if platform:
        state = "平台暂时成立"
    if breakout:
        state = "向上突破"
    if failure:
        state = "平台失效"
    plans = []
    for name, triggered, invalidated in (("回撤确认", pull, pull_failed or failure), ("平台突破", breakout, failure)):
        entry = invalid = trigger = pressure = None
        status, next_condition = "等待平台", "等待平台连续 5 日满足宽度与收盘稳定性条件"
        if platform:
            invalid = platform["low"]*floor_buffer
            if name == "平台突破":
                trigger = platform["high"]*buffer
                entry = trigger
                status, next_condition = "等待确认", "完整日 K 收盘超过平台上沿 0.5%"
            else:
                invalid = touch_low*floor_buffer if touch_low else None
                trigger = rebound*buffer if rebound else None
                entry = trigger
                status = "等待确认" if touch else "等待观察"
                next_condition = ("等待进入平台下沿 20% 区域" if not touch else
                                  "等待两日停止创新低" if not stop else
                                  "等待反弹高点确认" if not rebound else "等待收盘突破已确认反弹高点 0.5%")
            if entry:
                pressure = _pressure(parsed, cutoff, entry, peak)
        if triggered:
            entry, invalid, trigger, pressure = (triggered[k] for k in ("entry", "invalid", "trigger", "pressure"))
            status = "条件成立"
            next_condition = "记录后续失效" if name == "回撤确认" else ("已完成回踩确认" if retest else "等待回踩确认，突破事件已保留")
        if invalidated:
            status, next_condition = "已失效", "保留原事件，继续观察后续行情"
        plans.append({"name": name, "status": status, "next": next_condition,
                      "entry": str(entry) if entry else None, "invalid": str(invalid) if invalid else None,
                      "trigger": str(trigger) if trigger else None, "pressure": str(pressure) if pressure else None,
                      "entry_basis": "触发日收盘参考" if triggered else "未触发计划参考",
                      "triggered_at": triggered["date"].isoformat() if triggered else None,
                      "retest_at": retest.isoformat() if name == "平台突破" and retest else None,
                      "space": space(entry, invalid, pressure) if not invalidated else {"r": None, "reason": "方案已失效"}})
    representative = platform["representative"] if platform else None
    checks = [{"label": "高点后已有完整日 K", "value": str(since_peak)},
              {"label": "平台连续观察要求", "value": "5 日；影线总宽度 ≤12%；收盘跨度 ≤8%"},
              {"label": "确认边界", "value": "上沿/反弹高点 +0.5%；失效边界 -0.5%，均按收盘"},
              {"label": "压力来源", "value": "此前已确认的日线局部高点及本轮高点，取入场上方最近一个"}]
    if platform:
        checks += [{"label": "确认窗口影线宽度", "value": f'{platform["width_pct"]:.2f}%'},
                   {"label": "确认窗口收盘跨度", "value": f'{platform["close_span_pct"]:.2f}%'}]
    return {"version": VERSION, "rules": RULES, "state": state, "reason": "",
            "cutoff": cutoff.isoformat(), "anchor_date": anchor_date.isoformat(), "anchor_price": str(baseline),
            "peak": str(peak["high"]), "peak_date": peak["time"].isoformat(), "days_since_peak": since_peak,
            "consolidation_days": consolidation_days,
            "drawdown_pct": str((1-last["close"]/peak["high"])*100),
            "multiple": str(representative/baseline) if representative else None,
            "retained_pct": str((representative-baseline)/(peak["high"]-baseline)*100) if representative and peak["high"] > baseline else None,
            "platform": {k: v.isoformat() if isinstance(v, date) else str(v) for k, v in platform.items()} if platform else None,
            "observation_low": str(platform["low"]) if platform else None,
            "observation_high": str(platform["low"]+(platform["high"]-platform["low"])*D("0.2")) if platform else None,
            "failure_date": failure.isoformat() if failure else None,
            "plans": plans, "events": events, "checks": checks, "candles": rows}


def _pressure(rows, current_date, entry, peak):
    # Both pivot and its confirming right candle must already have completed.
    known = [r for r in rows if r["time"] < current_date]
    values = [known[i]["high"] for i in range(1, len(known)-1)
              if known[i]["high"] > known[i-1]["high"] and known[i]["high"] >= known[i+1]["high"]]
    if peak["time"] < current_date:
        values.append(peak["high"])
    above = [v for v in values if v > entry]
    return min(above) if above else None
