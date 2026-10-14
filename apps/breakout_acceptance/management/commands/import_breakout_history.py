import hashlib,json,shutil
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from apps.breakout_acceptance.services import storage_root
from apps.breakout_acceptance.models import ASSETS
class Command(BaseCommand):
    help='Verify and copy the first research raw snapshot into this project; no external runtime dependency.'
    def add_arguments(self,parser):parser.add_argument('--source',required=True)
    def handle(self,*args,**options):
        source=Path(options['source']).resolve()
        manifest=json.loads((source/'data'/'manifest.json').read_text())
        if {x['asset'] for x in manifest['coverage']}!=set(ASSETS):raise CommandError('Unexpected asset universe')
        target=storage_root();count=0
        for item in manifest['files']:
            relative=Path(item['path'])
            if len(relative.parts)<3 or relative.parts[0]!='raw' or relative.parts[1] not in (*ASSETS[:3],'gold'):raise CommandError('Unexpected raw path')
            original=(source/relative).resolve()
            if not original.is_relative_to(source):raise CommandError('Invalid raw path')
            if hashlib.sha256(original.read_bytes()).hexdigest()!=item['sha256']:raise CommandError('Snapshot checksum mismatch')
            dest=target/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,dest);count+=1
        self.stdout.write(self.style.SUCCESS(f'Imported {count} verified raw files into project storage'))
