"""Short, dated attention digest built only from official daily records."""

from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DailyMoverDigest, DailyMoverSnapshot, StructureObservation, TrendObservation
from .structure import VERSION as STRUCTURE_VERSION
from .trend import VERSION as TREND_VERSION


MAX_ITEMS = 5
MAX_ATTEMPTS = 3
MAX_TREND_ITEMS = 3
NOTABLE_STATUSES = {
    "突破待站稳": (0, "向上突破待站稳，仍需下一根完整日 K 收盘确认"),
    "跌破待站稳": (0, "向下跌破待站稳，仍需下一根完整日 K 收盘确认"),
    "突破未守住": (0, "向上突破未守住"),
    "跌破未守住": (0, "向下跌破未守住"),
    "回踩守住": (1, "回踩上侧边界后收盘守住"),
    "反抽未收复": (1, "反抽下侧边界后收盘仍在下方"),
    "向上站稳": (2, "连续两根日 K 收盘留在上侧边界外"),
    "向下保持": (2, "连续两根日 K 收盘留在下侧边界外"),
    "高位横盘": (3, "上侧边界外形成高位横盘"),
    "低位横盘": (3, "下侧边界外形成低位横盘"),
}


def _plans(observation):
    if observation is None:
        return {}
    continuation = observation.result.get("continuation") or {}
    return {plan.get("direction"): plan for plan in continuation.get("plans", [])}


def _trend_lines(day):
    candidates = []
    observations = TrendObservation.objects.filter(
        as_of_date=day, rule_version=TREND_VERSION,
        watch__discovered_on__lte=day,
        watch__archived_on__isnull=True,
    ).select_related("watch")
    for observation in observations:
        result = observation.result
        stage = result.get("stage")
        new_high = result.get("new_close_high_30d")
        new_watch = observation.watch.discovered_on == day
        stage_changed = result.get("stage_changed")
        if not (new_watch or new_high or stage_changed):
            continue
        if stage_changed and stage == "再次扩张":
            priority = 0
            event = "转入再次扩张"
        elif new_high and not new_watch:
            priority = 1
            event = "再创30日收盘新高"
        elif new_watch:
            priority = 2
            event = "新发现"
        elif stage_changed and stage in ("回升观察", "高位整理"):
            priority = 3
            event = f"转入{stage}"
        else:
            priority = 4
            event = f"转入{stage}"
        evidence = []
        if new_high and event != "再创30日收盘新高":
            evidence.append("30日收盘新高")
        high_count = result.get("close_high_14d_count") or 0
        if high_count:
            evidence.append(f"近30日14日收盘新高{high_count}次")
        if result.get("center_rising"):
            evidence.append("10日价格中心抬升")
        if result.get("lows_rising"):
            evidence.append("已确认低点抬升")
        if stage == "走弱":
            evidence.append(f"距此前30日收盘高点回撤{float(result.get('drawdown_from_prior_close_high_pct', 0)):.1f}%")
        suffix = f"；{'、'.join(evidence)}" if evidence else ""
        ranking = "榜内" if result.get("ranked") else "榜外"
        line = f"{observation.watch.symbol}（{ranking}）：{event}，当前{stage}{suffix}。"
        candidates.append((priority, bool(result.get("ranked")), -high_count,
                           observation.watch.discovered_on, observation.watch.symbol, line))
    candidates.sort()
    return [line for *_, line in candidates[:MAX_TREND_ITEMS]]


def build_digest(snapshot: DailyMoverSnapshot) -> str:
    day = snapshot.as_of_date
    entries = list(snapshot.entries.all())
    prior_day = day - timedelta(days=1)
    previous = DailyMoverSnapshot.objects.filter(as_of_date=prior_day).first()
    prior_entries = {
        entry.symbol: entry for entry in previous.entries.all()
    } if previous else {}
    symbols = {entry.symbol for entry in entries} | set(prior_entries)
    observations = StructureObservation.objects.filter(
        watch__origin="official",
        watch__symbol__in=symbols,
        as_of_date__in=(day, prior_day),
        rule_version=STRUCTURE_VERSION,
    ).select_related("watch").order_by("watch__discovered_on", "pk")
    by_day_symbol = {(row.as_of_date, row.watch.symbol): row for row in observations}
    missing = [entry.symbol for entry in entries if (day, entry.symbol) not in by_day_symbol]
    if missing:
        raise ValueError(f"结构观察尚未完成：{len(missing)} 个入榜合约")

    structure, risk, newcomers = [], [], []
    for entry in entries:
        current_plans = _plans(by_day_symbol.get((day, entry.symbol)))
        earlier_plans = _plans(by_day_symbol.get((prior_day, entry.symbol)))
        changes = []
        for direction, plan in current_plans.items():
            old = earlier_plans.get(direction)
            status = plan.get("status")
            if old and old.get("status") != status and status in NOTABLE_STATUSES:
                priority, description = NOTABLE_STATUSES[status]
                changes.append((priority, description))
        if changes:
            priority, description = min(changes)
            structure.append((priority, entry.rank, entry.symbol, f"{entry.symbol}：{description}。"))

        prior = prior_entries.get(entry.symbol)
        if prior:
            rank_drop = entry.rank - prior.rank
            crossed_drawdown = prior.drawdown_pct <= 30 < entry.drawdown_pct
            if rank_drop >= 5 or crossed_drawdown:
                details = []
                if rank_drop >= 5:
                    details.append(f"排名 #{prior.rank}→#{entry.rank}")
                if crossed_drawdown:
                    details.append(f"回撤首次超过 30%，现为 {entry.drawdown_pct:.1f}%")
                elif rank_drop >= 5:
                    details.append(f"距高点回撤 {entry.drawdown_pct:.1f}%")
                risk.append((-rank_drop, entry.rank, entry.symbol, f"{entry.symbol}：{'；'.join(details)}。"))
        elif entry.rank <= 10:
            pending = next((
                NOTABLE_STATUSES[plan.get("status")][1]
                for plan in current_plans.values()
                if plan.get("status") in {"突破待站稳", "跌破待站稳"}
            ), None)
            detail = (f"；{pending}" if pending else "")
            label = "新入榜" if previous else "首次正式榜单"
            newcomers.append((0 if pending else 1, entry.rank, entry.symbol,
                             f"{entry.symbol}：{label}第 {entry.rank}，{entry.signal}，"
                             f"区间涨幅 {entry.rise_pct:.1f}%，回撤 {entry.drawdown_pct:.1f}%{detail}。"))

    trend_lines = _trend_lines(day)
    rank_limit = MAX_ITEMS - len(trend_lines)
    selected = []
    used = set()
    section_limits = ((sorted(structure), 1), (sorted(risk), 1), (sorted(newcomers), 1)) if trend_lines else (
        (sorted(structure), 2), (sorted(risk), 1), (sorted(newcomers), 2))
    for candidates, limit in section_limits:
        if len(selected) >= rank_limit:
            break
        added = 0
        for _, _, symbol, line in candidates:
            if symbol in used or added >= limit or len(selected) >= rank_limit:
                continue
            selected.append(line)
            used.add(symbol)
            added += 1
    if len(selected) < rank_limit:
        for _, _, symbol, line in sorted(structure) + sorted(risk) + sorted(newcomers):
            if symbol not in used:
                selected.append(line)
                used.add(symbol)
            if len(selected) == rank_limit:
                break

    heading = (
        f"## 合约异动简报｜{day:%Y-%m-%d}\n"
        f"> 依据 {prior_day:%Y-%m-%d} UTC 完整日 K · "
        f"扫描 {snapshot.scanned_count} 个合约 · 入榜 {len(entries)}/30\n\n"
    )
    if trend_lines:
        trend_section = "### 趋势候选\n" + "\n".join(f"{index}. {line}" for index, line in enumerate(trend_lines, 1))
        rank_section = "\n\n### 异动榜变化\n" + ("\n".join(
            f"{index}. {line}" for index, line in enumerate(selected, 1)) if selected else "今日无新增重点变化。")
        lines = trend_section + rank_section
    else:
        lines = "\n".join(f"{index}. {line}" for index, line in enumerate(selected, 1))
    return heading + (lines if lines else "今日无新增重点变化。") + "\n\n<font color=\"comment\">仅供人工复核，不是交易指令。</font>"


def send_digest(snapshot: DailyMoverSnapshot, *, client: httpx.Client | None = None) -> DailyMoverDigest:
    digest = DailyMoverDigest.objects.filter(snapshot=snapshot).first()
    if digest is None:
        digest, _ = DailyMoverDigest.objects.get_or_create(
            snapshot=snapshot, defaults={"content": build_digest(snapshot)}
        )
    webhook_url = settings.DAILY_MOVER_WECHAT_WEBHOOK_URL
    if not webhook_url:
        if digest.status != DailyMoverDigest.Status.SENT:
            digest.status = DailyMoverDigest.Status.NOT_CONFIGURED
            digest.save(update_fields=["status", "updated_at"])
        return digest

    with transaction.atomic():
        digest = DailyMoverDigest.objects.select_for_update().get(pk=digest.pk)
        if digest.status in (
            DailyMoverDigest.Status.SENT,
            DailyMoverDigest.Status.SENDING,
            DailyMoverDigest.Status.UNKNOWN,
        ) or digest.attempts >= MAX_ATTEMPTS:
            return digest
        digest.status = DailyMoverDigest.Status.SENDING
        digest.attempts += 1
        digest.error_summary = ""
        digest.save(update_fields=["status", "attempts", "error_summary", "updated_at"])

    owns_client = client is None
    request_client = client or httpx.Client(timeout=10, trust_env=False)
    try:
        response = request_client.post(
            webhook_url,
            json={"msgtype": "markdown", "markdown": {"content": digest.content}},
        )
        response.raise_for_status()
        if response.json().get("errcode") != 0:
            digest.status = DailyMoverDigest.Status.REJECTED
            digest.error_summary = "WeCom rejected the message"
        else:
            digest.status = DailyMoverDigest.Status.SENT
            digest.sent_at = timezone.now()
    except httpx.HTTPStatusError as exc:
        digest.status = (DailyMoverDigest.Status.REJECTED if exc.response.status_code < 500
                         else DailyMoverDigest.Status.UNKNOWN)
        digest.error_summary = f"HTTP {exc.response.status_code}"
    except (httpx.RequestError, ValueError, TypeError, AttributeError):
        # A request timeout may mean the remote end accepted the message.
        # Avoid an automatic second push until its outcome is checked.
        digest.status = DailyMoverDigest.Status.UNKNOWN
        digest.error_summary = "Delivery outcome unknown; manual check required"
    finally:
        if owns_client:
            request_client.close()
    digest.save(update_fields=["status", "error_summary", "sent_at", "updated_at"])
    return digest
