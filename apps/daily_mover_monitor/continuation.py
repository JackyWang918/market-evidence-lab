"""Causal high/low boundaries for trend continuation, not entry plans."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from copy import deepcopy
import hashlib
import json

from .attention import _atr, _json, _pivots

D = Decimal
VERSION = "continuation-trial-v2"
RULES = {"hold_days": 2, "sideways_days": 3, "max_width_pct": "12",
         "max_close_span_pct": "8", "zone_atr": "0.25",
         "zone_min_pct": "0.5", "zone_max_pct": "3", "pivot_days": 2,
         "pivot_lookback_days": 30, "initial_history_days": 90}


def _empty(direction, reason):
    return {"direction": direction, "name": "向上延续" if direction == "long" else "向下延续",
            "zone": None, "status": "依据不足", "next": reason, "evidence": []}


def _zone(known, result, day, direction, previous=None):
    atr = _atr(known)
    if atr is None:
        return None
    kind = "high" if direction == "long" else "low"
    side = "高点" if direction == "long" else "低点"
    platform = result.get("platform")
    history = known[-(RULES["initial_history_days"] if previous is None else RULES["pivot_lookback_days"]):]
    pivots = _pivots(history, kind)
    if platform and platform["confirmed"] < day and (
        not result.get("failure_date") or result["failure_date"] >= day
    ) and (previous is None or platform["confirmed"] > previous["available"]):
        level, anchor, available = D(platform[kind]), platform["start"], platform["confirmed"]
        basis, confirmed = "已确认平台" + ("上沿" if kind == "high" else "下沿"), True
    elif pivots and (previous is None or pivots[-1]["confirmed_at"] > previous["available"]):
        pivot = pivots[-1]
        level, anchor, available = pivot[kind], pivot["time"], pivot["confirmed_at"]
        basis, confirmed = f"最近已确认日线{side}", True
    elif previous is not None:
        # Do not let ATR drift or a rolling window expiring silently move a boundary.
        return previous
    else:
        extreme = (max if kind == "high" else min)(history, key=lambda r: r[kind])
        level, anchor, available = extreme[kind], extreme["time"], known[-1]["time"]
        basis, confirmed = f"初始化历史{side} · 待验证", False
    half = min(level*D(RULES["zone_max_pct"])/100,
               max(level*D(RULES["zone_min_pct"])/100, atr*D(RULES["zone_atr"])))
    return {"level": level, "low": level-half, "high": level+half, "atr": atr,
            "anchor": anchor, "available": available, "basis": basis, "confirmed": confirmed}


def _new(direction, zone, day):
    plan = _empty(direction, "")
    plan.update(zone=zone, created_on=day, broken_on=None, confirmed_on=None,
                failed_on=None, outside_days=0, sideways=None, retest_on=None,
                preexisting_break=False)
    return plan


def _advance(plan, bar, streak, events):
    upward = plan["direction"] == "long"
    zone = plan["zone"]
    outside = bar["close"] > zone["high"] if upward else bar["close"] < zone["low"]
    action = "突破" if upward else "跌破"
    if not outside:
        if plan["broken_on"]:
            plan.update(status=f"{action}未守住", failed_on=bar["time"], outside_days=0, sideways=None)
            events.append({"date": bar["time"], "label": f'{plan["name"]}：收盘返回边界内，原延续依据未守住'})
        else:
            plan["status"] = "等待向上突破" if upward else "等待向下跌破"
        return []
    if not plan["broken_on"]:
        plan["broken_on"] = bar["time"]
        label = (f'{plan["name"]}：开始观察时已在边界外，边界冻结' if plan["preexisting_break"] else
                 f'{plan["name"]}：观察期内首次收盘{action}，边界冻结')
        events.append({"date": bar["time"], "label": label})
    streak = streak+[bar]
    plan["outside_days"] += 1
    plan["status"] = f"{action}待站稳"
    if len(streak) >= RULES["hold_days"]:
        if not plan["confirmed_on"]:
            plan["confirmed_on"] = bar["time"]
            events.append({"date": bar["time"], "label": f'{plan["name"]}：连续 {RULES["hold_days"]} 日收盘留在边界外'})
        plan["status"] = "向上站稳" if upward else "向下保持"
    overlaps = bar["low"] <= zone["high"] and bar["high"] >= zone["low"]
    if len(streak) >= 2 and overlaps:
        plan["retest_on"] = bar["time"]
        plan["status"] = "回踩守住" if upward else "反抽未收复"
    # Sideways requires the entire candle range to stay beyond the boundary.
    plan["sideways"] = None
    recent = streak[-RULES["sideways_days"]:]
    if len(recent) == RULES["sideways_days"]:
        low, high = min(r["low"] for r in recent), max(r["high"] for r in recent)
        closes = [r["close"] for r in recent]
        width, span = (high/low-1)*100, (max(closes)/min(closes)-1)*100
        beyond = low > zone["high"] if upward else high < zone["low"]
        if beyond and width <= D(RULES["max_width_pct"]) and span <= D(RULES["max_close_span_pct"]):
            plan["sideways"] = {"low": low, "high": high, "start": recent[0]["time"],
                                "end": bar["time"], "width_pct": width, "close_span_pct": span}
            plan["status"] = "高位横盘" if upward else "低位横盘"
    return streak[-RULES["sideways_days"]:]


def _finish(plan):
    if not plan or not plan["zone"]:
        return plan
    zone, upward = plan["zone"], plan["direction"] == "long"
    boundary = zone["high"] if upward else zone["low"]
    compare, action = ("高于", "突破") if upward else ("低于", "跌破")
    if plan["failed_on"]:
        plan["next"] = "收盘已返回边界内；下一根日 K 按此前已知高低点重新建立观察边界"
    elif plan["confirmed_on"]:
        plan["next"] = (f'已连续 {plan["outside_days"]} 日收盘{compare} {boundary:.8f}。'
                        f'这是已经越过的历史边界；下一步观察更{"高" if upward else "低"}位置的新高低点或横盘，'
                        '新的延续门槛确认后再更新。')
    else:
        plan["next"] = f'日 K 收盘需{compare} {boundary:.8f}，连续 {RULES["hold_days"]} 日确认；区间内横盘尚不算{action}'
    plan["boundary"] = boundary
    plan["evidence"] = [
        {"label": "区间依据", "value": f'{zone["basis"]} · {zone["anchor"]}'},
        {"label": "依据可用日期", "value": zone["available"]},
        {"label": "边界建立日期", "value": plan["created_on"]},
        {"label": "区间宽度", "value": "参考高/低点 ±0.25 ATR14，单侧限制 0.5%～3%"},
        {"label": "建立时 ATR14", "value": str(zone["atr"])},
        {"label": "历史与更新范围", "value": "初始化最多 90 根此前日 K；日常扫描近期 30 根的新确认拐点，保留已有参考位"},
    ]
    return plan


def _next_candidate(rows, plan):
    """Show an unconfirmed next hurdle without replacing the frozen prior plan."""
    if not plan or not plan["confirmed_on"] or plan["failed_on"]:
        return None
    upward = plan["direction"] == "long"
    kind = "high" if upward else "low"
    observed = [r for r in rows if r["time"] >= plan["broken_on"]]
    if not observed:
        return None
    extreme = (max if upward else min)(observed, key=lambda r: r[kind])
    level = extreme[kind]
    if (level <= plan["zone"]["high"] if upward else level >= plan["zone"]["low"]):
        return None
    atr = _atr(rows)
    if atr is None:
        return None
    half = min(level*D(RULES["zone_max_pct"])/100,
               max(level*D(RULES["zone_min_pct"])/100, atr*D(RULES["zone_atr"])))
    confirmed = any(p["time"] == extreme["time"] for p in _pivots(rows[-RULES["pivot_lookback_days"]:], kind))
    return {"level": level, "low": level-half, "high": level+half,
            "anchor": extreme["time"], "confirmed": confirmed}


def _history_hash(rows):
    return hashlib.sha256(json.dumps(_json(rows), sort_keys=True).encode()).hexdigest()


def _restore(value, key=None):
    if isinstance(value, dict):
        return {k: _restore(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v) for v in value]
    if value is not None and key in {"open", "high", "low", "close", "level", "atr", "boundary", "width_pct", "close_span_pct"}:
        return D(str(value))
    return value


def build_continuation(result, *, previous=None):
    output = {"version": VERSION, "rules": RULES, "cutoff": result["cutoff"],
              "provenance": "saved", "trend": "无法判断", "swing": "高低点尚不足以比较",
              "plans": [], "events": []}
    try:
        if result.get("state") == "数据不足":
            raise ValueError(result.get("reason") or "数据不足")
        rows = sorted(({"time": r["time"], **{k: D(str(r[k])) for k in ("open", "high", "low", "close")}}
                       for r in result.get("candles", []) if r["time"] <= result["cutoff"]), key=lambda r: r["time"])
        if len(rows) < 16 or rows[-1]["time"] != result["cutoff"]:
            raise ValueError("至少需要此前 15 根连续日 K 和截止日完整日 K")
        for i, bar in enumerate(rows):
            if any(not bar[k].is_finite() or bar[k] <= 0 for k in ("open", "high", "low", "close")) or (
                bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"])
            ):
                raise ValueError("日 K 价格无效")
            if i and date.fromisoformat(bar["time"]) != date.fromisoformat(rows[i-1]["time"])+timedelta(days=1):
                raise ValueError("日 K 不连续")
        last, prior = rows[-1], rows[-2]
        output["day_change_pct"] = (last["close"]/prior["close"]-1)*100
        output["last_close"] = last["close"]
        output["trend"] = "上涨中" if last["close"] > prior["close"] else "回落中" if last["close"] < prior["close"] else "收盘持平"
        start = (date.fromisoformat(result["discovered_on"])-timedelta(days=1)).isoformat() if result.get("discovered_on") else result["cutoff"]
        plans, streaks = {"long": None, "short": None}, {"long": [], "short": []}
        references = {"long": None, "short": None}
        pivots = {"high": [], "low": []}
        initialized_on = None
        # Resume only a contiguous saved v2 state with exactly matching earlier inputs.
        state = previous.get("state") if previous and previous.get("version") == VERSION else None
        if state and date.fromisoformat(previous["cutoff"])+timedelta(days=1) == date.fromisoformat(result["cutoff"]) and (
            state.get("history_hash") == _history_hash([r for r in rows if r["time"] <= previous["cutoff"]])
        ):
            restored = _restore(deepcopy(state))
            plans, streaks, references, pivots = (restored[k] for k in ("plans", "streaks", "references", "pivots"))
            initialized_on = restored["initialized_on"]
            start = result["cutoff"]
            output["events"] = deepcopy(previous["events"])
        for i, bar in enumerate(rows):
            if bar["time"] < start:
                continue
            known = rows[:i]
            if initialized_on is None and _atr(known) is not None:
                initialized_on = bar["time"]
                for kind in pivots:
                    pivots[kind] = _pivots(known[-RULES["initial_history_days"]:], kind)[-2:]
            for kind in pivots:
                # The current candle can confirm structure, but only subsequent bars use it as a boundary.
                for point in _pivots((known+[bar])[-5:], kind):
                    if not pivots[kind] or point["time"] > pivots[kind][-1]["time"]:
                        pivots[kind] = (pivots[kind]+[point])[-2:]
            for direction in plans:
                plan = plans[direction]
                zone = _zone(known, result, bar["time"], direction, references[direction])
                references[direction] = zone
                # A completed breakout can graduate to a newly confirmed, more
                # extreme pivot. Keep the old event, but show the next hurdle.
                advance_level = bool(plan and plan["confirmed_on"] and zone and zone["confirmed"] and
                                     zone["available"] > plan["confirmed_on"] and
                                     (zone["level"] > plan["zone"]["level"] if direction == "long" else
                                      zone["level"] < plan["zone"]["level"]))
                if not plan or not plan["broken_on"] or plan["failed_on"] or advance_level:
                    if not zone:
                        continue
                    if not plan or plan["failed_on"] or advance_level or zone != plan["zone"]:
                        if advance_level:
                            output["events"].append({
                                "date": bar["time"],
                                "label": (f'{plan["name"]}：原区间 {plan["zone"]["low"]:.8f}–'
                                          f'{plan["zone"]["high"]:.8f} 已确认越过；'
                                          f'按新确认的{"更高高点" if direction == "long" else "更低低点"}建立下一段观察区'),
                            })
                        plan = plans[direction] = _new(direction, zone, bar["time"])
                        if i:
                            previous_close = rows[i-1]["close"]
                            plan["preexisting_break"] = (previous_close > zone["high"] if direction == "long" else
                                                          previous_close < zone["low"])
                        streaks[direction] = []
                streaks[direction] = _advance(plan, bar, streaks[direction], output["events"])
        swing = []
        for kind, label in (("high", "高点"), ("low", "低点")):
            if len(pivots[kind]) >= 2:
                a, b = pivots[kind][-2:]
                swing.append(label+("抬高" if b[kind] > a[kind] else "降低" if b[kind] < a[kind] else "持平"))
        if swing:
            output["swing"] = " · ".join(swing)
        output["state"] = deepcopy({"plans": plans, "streaks": streaks, "references": references,
                                    "pivots": pivots, "initialized_on": initialized_on, "history_hash": _history_hash(rows)})
        output["plans"] = []
        for direction in plans:
            plan = _finish(plans[direction]) or _empty(direction, "等待足够的此前日 K 和高低点依据")
            candidate = _next_candidate(rows, plan)
            if candidate:
                plan["next_candidate"] = candidate
                qualifier = "已确认，下一根日 K 建立新观察区" if candidate["confirmed"] else "待右侧两根日 K 确认"
                plan["next"] += (f' 下一道观察候选{"高点" if direction == "long" else "低点"} '
                                 f'{candidate["level"]:.8f}（{qualifier}）；'
                                 '候选区间暂不作为正式突破条件。')
            output["plans"].append(plan)
    except (InvalidOperation, ValueError, KeyError, TypeError) as exc:
        output.update(trend="无法判断", events=[], plans=[_empty(d, str(exc)) for d in ("long", "short")])
    return _json(output)
