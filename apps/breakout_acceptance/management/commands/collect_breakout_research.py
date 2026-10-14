from datetime import date
from django.core.management.base import BaseCommand, CommandError
from apps.breakout_acceptance.services import queue_run,claim_work,execute_run
class Command(BaseCommand):
    help='Collect only SOL/BNB/ZEC/XAU closed candles and generate reproducible breakout research.'
    def add_arguments(self,parser):
        parser.add_argument('--offline',action='store_true')
        parser.add_argument('--end',type=date.fromisoformat)
    def handle(self,*args,**options):
        run,created=queue_run(cutoff=options['end'])
        if not created:raise CommandError('A research task is already queued or running')
        pk=claim_work()
        if pk!=run.pk:raise CommandError('Task could not be claimed')
        run=execute_run(pk,offline=options['offline'])
        self.stdout.write(self.style.SUCCESS(f'Research #{run.pk}: {run.status}; events={run.payload["event_count"]}'))
