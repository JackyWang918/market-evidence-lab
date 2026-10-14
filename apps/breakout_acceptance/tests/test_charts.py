from datetime import UTC, date, datetime, timedelta
from django.test import TestCase
from django.urls import reverse
from apps.breakout_acceptance.models import DailyCandle, ResearchEvent, ResearchRun

class ChartViewTests(TestCase):
    def setUp(self):
        self.run = ResearchRun.objects.create(status='partial', cutoff_date=date(2026,10,14), payload={'coverage':[{'asset':'SOL','end':'2026-10-13','lag_calendar_days':0},{'asset':'XAU','end':'2026-09-30','lag_calendar_days':13}], 'summary':[{'asset':'SOL','scale':'D','split':'holdout','horizon_bars':20,'accepted':True,'n':8,'median_return_atr':1.2,'median_dd_atr':2.1}]})
        for asset in ('SOL','BNB','XAU'):
            for day in (1,2,13,14):
                DailyCandle.objects.create(asset=asset,date=date(2026,10,day),open=100,high=110,low=90,close=105,source='test')
        self.event=ResearchEvent.objects.create(run=self.run, event_id='SOL-D-2026-10-02',asset='SOL',scale='D',known_at=datetime(2026,10,2,tzinfo=UTC),status='accepted',payload={'level':100,'atr_frozen':2},observations=[{'date':'2026-10-02','close_hold_fraction':1,'elapsed_calendar_days':1}],outcomes=[])
        self.other=ResearchEvent.objects.create(run=self.run,event_id='BNB-D-2026-10-02',asset='BNB',scale='D',known_at=datetime(2026,10,2,tzinfo=UTC),status='pending',payload={},observations=[],outcomes=[])

    def test_chart_is_separate_page_and_closed_asset_data_only(self):
        response=self.client.get(reverse('breakout_acceptance:charts'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'收盘与回撤底部')
        payload=response.context['chart_payload']
        self.assertEqual(payload['asset'],'SOL')
        self.assertEqual([row['date'] for row in payload['candles']],['2026-10-01','2026-10-02','2026-10-13'])
        self.assertEqual(payload['selected']['id'],self.event.pk)
        self.assertEqual(len(payload['summary']),1)
        # Starts are neutral: don't leak a later acceptance label into the origin marker.
        self.assertNotIn('status',payload['events'][0])

    def test_gold_defaults_to_week_and_shows_source_lag(self):
        response=self.client.get(reverse('breakout_acceptance:charts'),{'asset':'XAU'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['scale'],'W')
        self.assertContains(response,'归档日线滞后 13 天')
        self.assertEqual(self.client.get(reverse('breakout_acceptance:charts'),{'asset':'XAU','scale':'D'}).status_code,404)

    def test_invalid_asset_period_scale_and_foreign_event_rejected(self):
        for query in ({'asset':'OTHER'},{'period':'999999'},{'scale':'unknown'},{'event':str(self.other.pk)},{'event':'invalid'}):
            self.assertEqual(self.client.get(reverse('breakout_acceptance:charts'),query).status_code,404)

    def test_event_json_uses_snapshot_asset_and_preserves_unmatured_outcomes(self):
        url=reverse('breakout_acceptance:chart_event',args=(self.event.pk,))
        response=self.client.get(url,{'asset':'SOL'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['outcomes'],[])
        self.assertEqual(len(response.json()['observations']),1)
        self.assertEqual(self.client.get(url,{'asset':'BNB'}).status_code,404)
        self.assertEqual(self.client.get(url,{'asset':'OTHER'}).status_code,404)

    def test_previous_snapshot_event_cannot_be_mixed_with_latest(self):
        ResearchRun.objects.create(status='success',cutoff_date=date(2026,10,14))
        self.assertEqual(self.client.get(reverse('breakout_acceptance:chart_event',args=(self.event.pk,)),{'asset':'SOL'}).status_code,404)

    def test_no_collection_is_triggered_and_empty_page_works(self):
        count=ResearchRun.objects.count()
        self.client.get(reverse('breakout_acceptance:charts'))
        self.assertEqual(ResearchRun.objects.count(),count)
        ResearchRun.objects.all().delete()
        response=self.client.get(reverse('breakout_acceptance:charts'))
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.context['chart_payload']['selected'])
