from django.core.management.base import BaseCommand

from apps.daily_mover_monitor.service import scan


class Command(BaseCommand):
    help = "Generate today's immutable Binance USD-M daily mover snapshot."

    def handle(self, *args, **options):
        snapshot = scan()
        self.stdout.write(self.style.SUCCESS(
            f"{snapshot.as_of_date}: {snapshot.entries.count()} ranked / "
            f"{snapshot.scanned_count} scanned / {snapshot.candidate_count} candidates"
        ))
