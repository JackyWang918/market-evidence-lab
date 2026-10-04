import fcntl
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...live import advance_state, fetch_closed_candles, load_state, new_state, save_state


class Command(BaseCommand):
    help = "Track BTC 5m continuity predictions forward and settle them after each close."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=int, default=15)

    def handle(self, *args, **options):
        if not 5 <= options["poll_seconds"] <= 300:
            raise CommandError("--poll-seconds must be between 5 and 300")
        data_dir = Path(settings.BASE_DIR) / "data" / "btc_prediction" / "live"
        data_dir.mkdir(parents=True, exist_ok=True)
        state_path = data_dir / "continuity_live_v1.json"
        lock_path = data_dir / "continuity_live_v1.lock"
        proxy = os.getenv("BTC_PREDICTION_PROXY_URL") or settings.SOURCE_PROXY_URL
        with lock_path.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError("BTC continuity live worker is already running") from exc
            while True:
                now = datetime.now(UTC)
                try:
                    candles = fetch_closed_candles(proxy_url=proxy, now=now)
                    state = load_state(state_path)
                    if state is None:
                        state = new_state(now, candles)
                        save_state(state_path, state)
                    elif advance_state(state, candles, now):
                        save_state(state_path, state)
                    records = state["records"]
                    wins = sum(row["outcome"] == "win" for row in records)
                    losses = sum(row["outcome"] == "loss" for row in records)
                    pending = sum(row["outcome"] == "pending" for row in records)
                    self.stdout.write(
                        f"{now.isoformat()} live: wins={wins}, losses={losses}, "
                        f"pending={pending}, last={state['last_processed_open_time']}"
                    )
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    if options["once"]:
                        raise CommandError(str(exc)) from exc
                    self.stderr.write(f"Live poll failed: {exc}")
                if options["once"]:
                    return
                time.sleep(options["poll_seconds"])
