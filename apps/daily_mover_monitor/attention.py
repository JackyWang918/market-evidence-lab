"""Causal daily long/short observation plans, independent of platform formation."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

D = Decimal
VERSION = "attention-trial-v1"
RULES = {"atr_days": 14, "zone_atr": "0.25", "zone_min_pct": "0.5",
         "zone_max_pct": "3", "buffer_pct": "0.5", "entry_atr": "0.1",
         "pivot_right_days": 2, "fallback_days": 10, "pivot_lookback_days": 30}


def _empty(direction, reason):
    return {"direction": direction, "name": "多头止跌确认" if direction == "long" else "空头跌破反抽",
            "status": "依据不足", "next": reason, "zone": None, "entry_zone": None,
            "entry": None, "invalid": None, "trigger": None, "target": None,
            "r": None, "r_reason": reason, "evidence": [], "triggered_at": None}


def _atr(known):
    if len(known) < RULES["atr_days"]+1:
        return None
    pairs = list(zip(known[:-1], known[1:]))[-RULES["atr_days"]:]
    return sum(max(b["high"]-b["low"], abs(b["high"]-a["close"]), abs(b["low"]-a["close"]))
               for a, b in pairs)/D(RULES["atr_days"])


def _pivots(rows, kind, right=2):
    result = []
    for i in range(2, len(rows)-right):
        value = rows[i][kind]
        neighbors = rows[i-2:i]+rows[i+1:i+right+1]
        qualifies = all(value <= r[kind] for r in neighbors) if kind == "low" else all(value >= r[kind] for r in neighbors)
        strict = any(value < r[kind] for r in neighbors) if kind == "low" else any(value > r[kind] for r in neighbors)
        if qualifies and strict:
            result.append({**rows[i], "confirmed_at": rows[i+right]["time"]})
    return result


def _proposal(known, result, current_day):
    atr = _atr(known)
    if atr is None:
        return None
    platform = result.get("platform")
    if platform and platform["confirmed"] < current_day and (
        not result.get("failure_date") or result["failure_date"] >= current_day
    ):
        level, anchor, available, basis, confirmed = (D(platform["low"]), platform["start"],
                                                    platform["confirmed"], "已确认平台下沿", True)
    else:
        cycle = [r for r in known if r["time"] >= result.get("anchor_date", known[0]["time"])]
        if not cycle:
            return None
        peak = max(cycle, key=lambda r: r["high"])
        post_peak = [r for r in cycle if r["time"] > peak["time"]]
        pivots = _pivots(known[-RULES["pivot_lookback_days"]:], "low")
        if post_peak:
            anchor_row = min(post_peak, key=lambda r: r["low"])
            matching = next((p for p in pivots if p["time"] == anchor_row["time"]), None)
            confirmed = matching is not None
            level, anchor = anchor_row["low"], anchor_row["time"]
            available = matching["confirmed_at"] if matching else anchor
            basis = "已确认回撤低点" if confirmed else "回撤低点 · 待验证"
        elif pivots:
            anchor_row = pivots[-1]
            level, anchor, available = anchor_row["low"], anchor_row["time"], anchor_row["confirmed_at"]
            basis, confirmed = "已确认日线局部低点", True
        else:
            anchor_row = min(known[-RULES["fallback_days"]:], key=lambda r: r["low"])
            level, anchor, available = anchor_row["low"], anchor_row["time"], known[-1]["time"]
            basis, confirmed = "最近 10 日窗口低点 · 待验证", False
    half = min(level*D(RULES["zone_max_pct"])/100,
               max(level*D(RULES["zone_min_pct"])/100, atr*D(RULES["zone_atr"])))
    return {"level": level, "low": level-half, "high": level+half, "atr": atr,
            "anchor": anchor, "available": available, "basis": basis, "confirmed": confirmed}


def _new(direction, proposal, current_day):
    plan = _empty(direction, "等待入场确认")
    plan.update(zone=proposal, created_on=current_day, touch=None, broken_on=None, failed_on=None,
                rebound=None, status="等待观察" if direction == "long" else "等待跌破",
                invalid=proposal["low"]-proposal["level"]*D(RULES["buffer_pct"])/100 if direction == "long" else None)
    return plan


def _target(known, entry, direction):
    kind = "high" if direction == "long" else "low"
    values = [p[kind] for p in _pivots(known, kind)]
    eligible = [v for v in values if v > entry] if direction == "long" else [v for v in values if v < entry]
    return (min(eligible) if direction == "long" else max(eligible)) if eligible else None


def _trigger(plan, bar, known, events):
    plan.update(status="条件成立", triggered_at=bar["time"], entry=bar["close"],
                target=_target(known, bar["close"], plan["direction"]))
    half = plan["zone"]["atr"]*D(RULES["entry_atr"])
    # Keep the proposed execution band strictly inside the valid risk/target ordering.
    risk = abs(plan["entry"]-plan["invalid"])
    half = min(half, risk/D(4), plan["entry"]*D("0.03"))
    if plan["target"] is not None:
        half = min(half, abs(plan["target"]-plan["entry"])/D(4))
    plan["entry_zone"] = {"low": plan["entry"]-half, "high": plan["entry"]+half}
    events.append({"date": bar["time"], "label": f'{plan["name"]}：条件成立，参考价格冻结'})


def _advance(plan, bar, known, events):
    zone, direction = plan["zone"], plan["direction"]
    if plan["failed_on"]:
        return
    if plan["triggered_at"]:
        invalid = bar["close"] < plan["invalid"] if direction == "long" else bar["close"] > plan["invalid"]
        if invalid:
            plan.update(status="已失效", failed_on=bar["time"])
            events.append({"date": bar["time"], "label": f'{plan["name"]}：收盘越过失效价'})
        return
    overlaps = bar["low"] <= zone["high"] and bar["high"] >= zone["low"]
    if direction == "long":
        if bar["close"] < plan["invalid"]:
            plan.update(status="已失效", failed_on=bar["time"])
            events.append({"date": bar["time"], "label": "多头观察依据失效，保留原区间"})
            return
        if overlaps and not plan["touch"]:
            plan.update(touch=bar["time"], status="观察中")
            events.append({"date": bar["time"], "label": "进入多头注意力区间，仅开始观察"})
        if not plan["touch"]:
            return
        # A rebound high is usable only after its right-hand candle has completed.
        if len(known) >= 3:
            left, pivot, right = known[-3:]
            if pivot["time"] >= plan["touch"] and pivot["high"] > left["high"] and pivot["high"] >= right["high"]:
                plan["rebound"] = pivot["high"]*(1+D(RULES["buffer_pct"])/100)
                plan["trigger"] = plan["rebound"]
        recent = (known+[bar])[-3:]
        stopped = len(recent) == 3 and recent[0]["time"] >= plan["touch"] and recent[0]["low"] <= recent[1]["low"] <= recent[2]["low"]
        if stopped and plan["rebound"] and bar["close"] > max(plan["rebound"], zone["high"]):
            _trigger(plan, bar, known, events)
    else:
        plan["trigger"] = zone["low"]
        if not plan["broken_on"]:
            if bar["close"] < zone["low"]:
                plan.update(broken_on=bar["time"], status="等待反抽")
                events.append({"date": bar["time"], "label": "收盘跌破空头观察区下沿，等待后续反抽"})
            return
        if bar["close"] > zone["high"]:
            plan.update(status="已失效", failed_on=bar["time"])
            events.append({"date": bar["time"], "label": "空头跌破依据失效，收盘收复区间"})
            return
        if overlaps:
            plan.update(touch=bar["time"], status="等待确认")
            if bar["time"] > plan["broken_on"] and bar["close"] < zone["low"] and bar["close"] < bar["open"]:
                plan["invalid"] = max(bar["high"], zone["high"])+zone["level"]*D(RULES["buffer_pct"])/100
                _trigger(plan, bar, known, events)


def _finish(plan, last):
    if plan is None:
        return None
    zone = plan["zone"]
    if plan["failed_on"]:
        plan["next"] = "原方案已失效；等待新的已确认低点建立下一套观察计划"
        plan["r_reason"] = "方案已失效"
    elif plan["triggered_at"]:
        plan["next"] = "日线条件已成立；入场区域为触发日参考，继续核对失效条件"
        risk = plan["entry"]-plan["invalid"] if plan["direction"] == "long" else plan["invalid"]-plan["entry"]
        reward = (plan["target"]-plan["entry"] if plan["direction"] == "long" else plan["entry"]-plan["target"]) if plan["target"] else None
        if risk <= 0:
            plan["r_reason"] = "缺少有效风险距离"
        elif reward is None or reward <= 0:
            plan["r_reason"] = "未识别有效目标参考位"
        else:
            plan.update(r=reward/risk, r_reason="", risk_pct=risk/plan["entry"]*100,
                        space_pct=reward/plan["entry"]*100)
    elif plan["direction"] == "long":
        plan["next"] = ("等待价格进入区间，再检查止跌和反弹高点" if not plan["touch"] else
                        "已进入观察区，等待连续两日不创新低，并收盘突破确认的反弹高点 0.5%")
    elif not plan["broken_on"]:
        plan["next"] = f'等待日 K 收盘跌破 {zone["low"]:.8f}，随后观察反抽'
    else:
        plan["next"] = "等待后续日 K 反抽进入区间，收盘回落至下沿之下且收阴"
    if not plan["triggered_at"] and not plan["failed_on"]:
        plan["r_reason"] = "尚未确认入场，不计算 R"
    plan["distance_pct"] = (last["close"]/zone["level"]-1)*100
    plan["evidence"] = [
        {"label": "区间依据", "value": f'{zone["basis"]} · {zone["anchor"]}'},
        {"label": "依据可用日期", "value": zone["available"]},
        {"label": "本计划建立日期", "value": plan["created_on"]},
        {"label": "区间宽度", "value": "参考位 ±0.25 ATR(14)，单侧限制在价格的 0.5%～3%"},
        {"label": "建立时 ATR(14)", "value": str(zone["atr"])},
        {"label": "参考价性质", "value": "入场区域在确认后计算；日线收盘参考，不保证实际成交"},
    ]
    return plan


def _json(value):
    if isinstance(value, D):
        return str(value)
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    return value


def build_attention(result):
    output = {"version": VERSION, "rules": RULES, "cutoff": result["cutoff"], "events": [], "trend": "无法判断",
              "plans": [], "provenance": "saved"}
    reason = result.get("reason") if result.get("state") == "数据不足" else None
    rows = []
    try:
        rows = sorted(({"time": r["time"], **{k: D(str(r[k])) for k in ("open", "high", "low", "close")}}
                       for r in result.get("candles", []) if r["time"] <= result["cutoff"]), key=lambda r: r["time"])
        if not rows or rows[-1]["time"] != result["cutoff"]:
            raise ValueError("缺少截止日完整日 K")
        for i, row in enumerate(rows):
            values = [row[k] for k in ("open", "high", "low", "close")]
            if any(not v.is_finite() or v <= 0 for v in values) or row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
                raise ValueError("日 K 价格无效")
            if i and date.fromisoformat(row["time"]) != date.fromisoformat(rows[i-1]["time"])+timedelta(days=1):
                raise ValueError("日 K 不连续")
        if len(rows) >= 2:
            last, prior = rows[-1], rows[-2]
            new_high = last["high"] > max(r["high"] for r in rows[-11:-1])
            output["trend"] = ("创新高上涨" if new_high and last["close"] > prior["close"] else
                               "冲高回落" if new_high and last["close"] < prior["close"] else
                               "反弹中" if last["close"] > prior["close"] else
                               "回撤中" if last["close"] < prior["close"] else "收盘持平")
            output["day_change_pct"] = str((last["close"]/prior["close"]-1)*100)
        start = (date.fromisoformat(result["discovered_on"])-timedelta(days=1)).isoformat() if result.get("discovered_on") else result["cutoff"]
        plans = {"long": None, "short": None}
        events = output["events"]
        for i, bar in enumerate(rows):
            if bar["time"] < start:
                continue
            known = rows[:i]
            proposal = _proposal(known, result, bar["time"])
            for direction, plan in list(plans.items()):
                # Active/touched levels freeze. Inactive plans may follow new evidence.
                idle = plan and not plan["touch"] and not plan["broken_on"] and not plan["triggered_at"] and not plan["failed_on"]
                rearm = plan and plan["failed_on"] and proposal and proposal["confirmed"] and proposal["available"] > plan["failed_on"]
                replace = proposal and idle and proposal["anchor"] != plan["zone"]["anchor"]
                if proposal and (plan is None or rearm or replace):
                    plan = plans[direction] = _new(direction, proposal, bar["time"])
                    events.append({"date": bar["time"], "label": f'{plan["name"]}：建立观察区间'})
                    # Creation can record touch/break/invalidity, but confirmation requires later bars.
                    _advance(plan, bar, known, events)
                    continue
                if plan:
                    _advance(plan, bar, known, events)
        output["plans"] = [_finish(plans[d], rows[-1]) or _empty(d, "至少需要 15 根连续日 K 计算 ATR 和参考区间") for d in ("long", "short")]
    except (InvalidOperation, ValueError, KeyError, TypeError) as exc:
        reason = str(exc)
    if reason:
        output["plans"] = [_empty(d, reason) for d in ("long", "short")]
        output["events"] = []
        output["trend"] = "无法判断"
    return _json(output)
