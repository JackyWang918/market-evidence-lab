"""Explain published judgments from their frozen inputs without changing them."""
from copy import deepcopy
from decimal import Decimal
from .attention import build_attention
from .continuation import build_continuation


def present_structure(result):
    result = deepcopy(result)
    platform = result.get("platform")
    state = result["state"]
    view = {"label": "尚未形成平台", "tone": "pending", "low": None, "high": None,
            "summary": "", "reasons": [], "metrics": []}
    if platform:
        failed = state == "平台失效"
        view.update(label="已失效平台" if failed else "已确认平台", tone="failed" if failed else "confirmed",
                    low=platform["low"], high=platform["high"],
                    summary=(f'失效于 {result.get("failure_date", "")}' if failed else
                             f'确认于 {platform["confirmed"]} · 边界已冻结'))
    elif state == "数据不足":
        view.update(label="数据不足", summary=result.get("reason") or "等待完整结构数据")
    else:
        rules = result["rules"]
        required = int(rules["platform_days"])
        rows = sorted((r for r in result.get("candles", [])
                       if result["peak_date"] < r["time"] <= result["cutoff"]), key=lambda r: r["time"])
        recent = rows[-required:]
        if not recent:
            view["summary"] = "最新日 K 创出本轮新高，尚无高点后整理"
        else:
            low = min(Decimal(r["low"]) for r in recent)
            high = max(Decimal(r["high"]) for r in recent)
            closes = [Decimal(r["close"]) for r in recent]
            width = (high/low-1)*100
            span = (max(closes)/min(closes)-1)*100
            days = len(recent)
            view["metrics"] = [
                {"label": "高点后最近观察窗口", "value": f'{recent[0]["time"]} 至 {recent[-1]["time"]} · {days}/{required} 根'},
                {"label": "观察窗口影线总宽度", "value": f'{width:.2f}% / 门槛 ≤{rules["max_width_pct"]}%'},
                {"label": "观察窗口收盘跨度", "value": f'{span:.2f}% / 门槛 ≤{rules["max_close_span_pct"]}%'},
            ]
            if days < required:
                view["reasons"].append(f"高点后仅 {days}/{required} 根完整日 K")
            if width > Decimal(rules["max_width_pct"]):
                view["reasons"].append(f'影线宽度 {width:.2f}% > {rules["max_width_pct"]}%')
            if span > Decimal(rules["max_close_span_pct"]):
                view["reasons"].append(f'收盘跨度 {span:.2f}% > {rules["max_close_span_pct"]}%')
            if state == "平台形成中":
                # Only the already qualifying trailing consolidation is provisional.
                candidate = rows[-min(int(result["consolidation_days"]), required):]
                view.update(label="暂定整理范围 · 未确认", tone="provisional",
                            low=str(min(Decimal(r["low"]) for r in candidate)),
                            high=str(max(Decimal(r["high"]) for r in candidate)))
                view["reasons"].insert(0, f'当前连续整理 {result["consolidation_days"]}/{required} 日')
            view["summary"] = "；".join(view["reasons"]) or "尚未满足平台确认条件"
    result["platform_view"] = view
    if "continuation" not in result:
        result["continuation"] = build_continuation(result)
        result["continuation"]["provenance"] = "recomputed"
    if "attention" not in result:
        result["attention"] = build_attention(result)
        result["attention"]["provenance"] = "recomputed"
    if not platform:
        for plan in result["plans"]:
            plan["next"] = "先确认平台，暂不生成该方案参考价"
    return result
