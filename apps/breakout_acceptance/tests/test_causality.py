import unittest
import pandas as pd
import numpy as np
from apps.breakout_acceptance.engine import features,aggregate,outcome,research,ROOT

class CausalityTests(unittest.TestCase):
 def setUp(self):
  idx=pd.date_range('2020-01-01',periods=180,tz='UTC')
  close=100+np.arange(180)*.12+np.sin(np.arange(180))*2
  self.b=pd.DataFrame({'open':close-.1,'high':close+1,'low':close-1,'close':close},index=idx)
 def test_prefix_features(self):
  full=features(aggregate(self.b,'D',pd.Timestamp('2021-01-01',tz='UTC')),20)
  for n in (30,60,100):
   small=features(aggregate(self.b.iloc[:n],'D',pd.Timestamp('2021-01-01',tz='UTC')),20)
   pd.testing.assert_frame_equal(full.iloc[:n],small)
 def test_future_mutation_does_not_change_past(self):
  changed=self.b.copy(); changed.iloc[100:]*=10
  pd.testing.assert_frame_equal(features(self.b,20).iloc[:100],features(changed,20).iloc[:100])
 def test_incomplete_week_month_excluded(self):
  end=pd.Timestamp('2020-06-18',tz='UTC')
  for scale in ('W','M'):
   bars=aggregate(self.b[self.b.index<end],scale,end)
   self.assertTrue((bars.available<=end).all())
 def test_source_lag_excludes_partial_week(self):
  daily=self.b[self.b.index<pd.Timestamp('2020-06-18',tz='UTC')]
  w=aggregate(daily,'W',pd.Timestamp('2020-07-01',tz='UTC'))
  self.assertTrue((w.available<=pd.Timestamp('2020-06-18',tz='UTC')).all())
 def test_quarantine_blocks_contaminated_period(self):
  b=self.b.copy();b.attrs['invalid_dates']=[pd.Timestamp('2020-03-05',tz='UTC')]
  w=aggregate(b,'W',pd.Timestamp('2021-01-01',tz='UTC'))
  self.assertTrue(pd.isna(w.loc[pd.Timestamp('2020-03-08',tz='UTC'),'close']))
 def test_manual_outcome_and_censor(self):
  b=pd.DataFrame({'open':[100,100,110],'close':[100,120,90],'low':[99,95,85],'high':[101,121,111]})
  x=outcome(b,0,2,10)
  self.assertAlmostEqual(x['return_pct'],-10)
  self.assertAlmostEqual(x['return_atr'],-1)
  self.assertAlmostEqual(x['max_dd_pct'],25)
  self.assertAlmostEqual(x['max_dd_atr'],3)
  self.assertIsNone(outcome(b,1,2,10))
 def test_breakout_uses_previous_high_and_atr(self):
  b=self.b.copy(); b.iloc[40,b.columns.get_loc('close')]=200; b.iloc[40,b.columns.get_loc('high')]=201
  f=features(b,20)
  self.assertTrue(f.iloc[40].breakout)
  self.assertEqual(f.iloc[40].level,b.iloc[20:40].high.max())
if __name__=='__main__':unittest.main()
