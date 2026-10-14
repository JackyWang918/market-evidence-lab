from datetime import UTC,datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
from django.test import TestCase,SimpleTestCase,override_settings
from django.db import IntegrityError,transaction
from django.core.management import call_command
from django.urls import reverse
from django.test import Client
from apps.breakout_acceptance.models import ResearchRun,ResearchSchedule,DailyCandle,ResearchEvent
from apps.breakout_acceptance.services import get_schedule,queue_run,claim_work,execute_run

NOW=datetime(2026,10,14,0,20,tzinfo=UTC)

class ScheduleTests(TestCase):
 def test_default_is_program_schedule_at_0820(self):
  s=get_schedule();self.assertTrue(s.enabled);self.assertEqual(s.run_time.isoformat(),'08:20:00');self.assertEqual(s.timezone,'Asia/Shanghai')
 def test_before_due_then_single_claim_and_next_day(self):
  s=get_schedule();s.next_run_at=NOW;s.save()
  self.assertIsNone(claim_work(now=NOW-timedelta(seconds=1)))
  pk=claim_work(now=NOW);self.assertIsNotNone(pk)
  self.assertIsNone(claim_work(now=NOW));self.assertEqual(ResearchRun.objects.count(),1)
  s.refresh_from_db();self.assertEqual(s.next_run_at,NOW+timedelta(days=1))
  self.assertEqual(ResearchRun.objects.get().cutoff_date,NOW.date())
 def test_manual_queue_is_idempotent_and_excludes_scheduled_overlap(self):
  a,new=queue_run();b,new2=queue_run();self.assertTrue(new);self.assertFalse(new2);self.assertEqual(a.pk,b.pk)
  self.assertEqual(claim_work(),a.pk);self.assertIsNone(claim_work())
 def test_database_disallows_parallel_active_runs(self):
  queue_run()
  with self.assertRaises(IntegrityError),transaction.atomic():ResearchRun.objects.create(trigger='scheduled',cutoff_date=NOW.date())
 def test_disabled_schedule_does_not_collect_but_manual_queue_can_run(self):
  s=get_schedule();s.enabled=False;s.next_run_at=NOW-timedelta(days=1);s.save()
  self.assertIsNone(claim_work(now=NOW))
  run,_=queue_run();self.assertEqual(claim_work(now=NOW),run.pk)
 def test_failure_preserves_previous_result_and_sanitizes_error(self):
  old=ResearchRun.objects.create(cutoff_date=NOW.date(),status='success',payload={'event_count':1})
  run,_=queue_run();claim_work()
  with TemporaryDirectory() as folder,override_settings(BASE_DIR=Path(folder)),patch('apps.breakout_acceptance.engine.collect',side_effect=RuntimeError('private-secret')),self.assertLogs('apps.breakout_acceptance.services',level='ERROR'):
   with self.assertRaises(RuntimeError):execute_run(run.pk)
  run.refresh_from_db();self.assertEqual(run.status,'failed');self.assertNotIn('private-secret',run.safe_error)
  self.assertEqual(ResearchRun.objects.filter(status='success').first().pk,old.pk)
 def test_unified_scheduler_dispatches_research(self):
  with patch('apps.scheduling.management.commands.run_scheduler.execute_breakout_due') as execute:
   call_command('run_scheduler','--once')
  execute.assert_called_once();self.assertIn('heartbeat_callback',execute.call_args.kwargs)

class ViewTests(TestCase):
 def setUp(self):
  self.run=ResearchRun.objects.create(status='partial',cutoff_date=NOW.date(),payload={'coverage':[{'asset':'SOL','start':'2020-08-11','end':'2026-10-13','bars':10,'lag_calendar_days':0}],'manifest':{'quarantined_rows':0},'summary':[]})
  self.event=ResearchEvent.objects.create(run=self.run,event_id='SOL-D-2026-10-01',asset='SOL',scale='D',known_at=NOW,status='accepted',payload={'level':100,'atr_frozen':2},observations=[],outcomes=[])
  DailyCandle.objects.create(asset='SOL',date=NOW.date()-timedelta(days=1),open=1,high=2,low=.5,close=1.5,source='binance_spot')
 def test_pages_expose_monitor_data_event_and_run(self):
  for name,args in [('index',()),('data',()),('event',(self.event.pk,)),('run',(self.run.pk,))]:
   self.assertEqual(self.client.get(reverse('breakout_acceptance:'+name,args=args)).status_code,200)
  self.assertContains(self.client.get(reverse('breakout_acceptance:index')),'每日数据采集与研究更新')
  self.assertContains(self.client.get(reverse('scheduling:index')),'突破接受度每日研究')
 def test_other_asset_query_is_rejected(self):
  for name in ('index','data'):
   self.assertEqual(self.client.get(reverse('breakout_acceptance:'+name),{'asset':'UNSUPPORTED'}).status_code,404)
 def test_collection_is_post_only_and_queued_not_executed_in_view(self):
  url=reverse('breakout_acceptance:configure')
  self.assertEqual(self.client.get(url).status_code,405)
  self.assertEqual(self.client.post(url,{'action':'collect'}).status_code,302)
  self.assertEqual(ResearchRun.objects.filter(status='queued').count(),1)
  self.assertEqual(Client(enforce_csrf_checks=True).post(url,{'action':'disable'}).status_code,403)
 def test_toggle_changes_database_schedule(self):
  self.client.post(reverse('breakout_acceptance:configure'),{'action':'disable'})
  self.assertFalse(get_schedule().enabled)
 def test_database_asset_whitelist(self):
  with self.assertRaises(IntegrityError),transaction.atomic():
   DailyCandle.objects.create(asset='OTHER',date=NOW.date(),open=1,high=1,low=1,close=1,source='test')
 def test_missing_or_unapproved_download_is_rejected(self):
  self.assertEqual(self.client.get(reverse('breakout_acceptance:download',args=(self.run.pk,'secret'))).status_code,404)

class CollectorTests(SimpleTestCase):
 def test_gold_source_outage_preserves_collected_crypto(self):
  import pandas as pd
  from apps.breakout_acceptance.engine import collect
  base=int(datetime(1999,1,1,tzinfo=UTC).timestamp()*1000)
  candles=[[base+i*86400000,'100','110','90','105','1',base+(i+1)*86400000-1] for i in range(3)]
  def fake_fetch(url,path,offline=False):
   if 'fapi.binance.com' in url: raise OSError('source unavailable')
   return json.dumps(candles).encode()
  with TemporaryDirectory() as folder,patch('apps.breakout_acceptance.engine.fetch',side_effect=fake_fetch):
   data=collect(pd.Timestamp('2000-01-01',tz='UTC'),root=folder)
   self.assertEqual(tuple(data),('SOL','BNB','ZEC'))
   manifest=json.loads((Path(folder)/'data'/'manifest.json').read_text())
   self.assertEqual([row['asset'] for row in manifest['missing_assets']],['XAU'])
   self.assertEqual([row['asset'] for row in manifest['coverage']],['SOL','BNB','ZEC'])
   self.assertEqual(list(pd.read_csv(Path(folder)/'data'/'quarantine.csv').columns),['asset','date','open','close','low','high'])
 def test_completed_only_and_fixed_universe(self):
  import pandas as pd
  from apps.breakout_acceptance.engine import collect
  base=int(datetime(1999,1,1,tzinfo=UTC).timestamp()*1000)
  candles=[[base+i*86400000,'100','110','90','105','1',base+(i+1)*86400000-1] for i in range(3)]
  future=int(datetime(2000,1,1,tzinfo=UTC).timestamp()*1000)
  candles.append([future,'100','110','90','105','1',future+86400000-1])
  def fake_fetch(url,path,offline=False):return json.dumps(candles).encode()
  with TemporaryDirectory() as folder,patch('apps.breakout_acceptance.engine.fetch',side_effect=fake_fetch):
   data=collect(pd.Timestamp('2000-01-01',tz='UTC'),root=folder)
   self.assertEqual(tuple(data),('SOL','BNB','ZEC','XAU'))
   for frame in data.values():self.assertEqual(len(frame),3);self.assertLess(frame.index.max(),pd.Timestamp('2000-01-01',tz='UTC'))
   manifest=json.loads((Path(folder)/'data'/'manifest.json').read_text())
   gold=next(row for row in manifest['coverage'] if row['asset']=='XAU')
   self.assertEqual((gold['symbol'],gold['market']),('XAUUSDT','binance_usdm_futures'))
   self.assertIn('fapi.binance.com',next(row['url'] for row in manifest['files'] if row['path'].startswith('raw/XAU/')))
