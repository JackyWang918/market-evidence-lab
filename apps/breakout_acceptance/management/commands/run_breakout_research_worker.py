import signal,threading
from django.core.management.base import BaseCommand
from apps.breakout_acceptance.services import execute_due,get_schedule
class Command(BaseCommand):
    help='Project worker for the four-asset breakout research schedule (also supported by run_scheduler).'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
        get_schedule()
        while not stop.is_set():
            try:execute_due()
            except Exception as exc:self.stderr.write(f'Research task failed ({type(exc).__name__}); retry available in the project.')
            if options['once']:break
            stop.wait(30)
