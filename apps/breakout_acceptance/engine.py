from pathlib import Path

# V1 immutable rules; this module does not import generic market datasets.
import argparse, datetime as dt, hashlib, json, urllib.request, time, lzma, struct
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parent
ASSETS=('SOL','BNB','ZEC','XAU')
UTC=dt.timezone.utc

def fetch(url,path,offline=False):
    if path.exists(): return path.read_bytes()
    if offline: raise FileNotFoundError(path)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url,timeout=35) as r: b=r.read()
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_bytes(b);temporary.replace(path)
            return b
        except Exception:
            if attempt==3: raise
            time.sleep(2**attempt)

def collect(end,offline=False,root=None):
    ROOT=Path(root) if root is not None else Path(__file__).resolve().parents[2]/"data"/"breakout_acceptance"
    manifest={'end_exclusive_utc':str(end),'retrieved_utc':dt.datetime.now(UTC).isoformat(),'files':[],'coverage':[],'missing_assets':[]}
    datasets={}; quarantine=[]
    for asset in ASSETS:
        rows=[]
        if asset!='XAU':
            start=0; until=int(end.timestamp()*1000)-1
            while start<=until:
                url=f'https://data-api.binance.vision/api/v3/klines?symbol={asset}USDT&interval=1d&timeZone=0&startTime={start}&endTime={until}&limit=1000'
                path=ROOT/'raw'/asset/f'{start}_{until}.json'
                b=fetch(url,path,offline); page=json.loads(b)
                manifest['files'].append({'path':str(path.relative_to(ROOT)),'url':url,'sha256':hashlib.sha256(b).hexdigest()})
                if not page: break
                for r in page:
                    # API defaults to milliseconds; normalize defensively, unlike archive's 2025 microseconds.
                    stamp=int(r[0]); stamp=stamp//1000 if stamp>10**14 else stamp
                    close_stamp=int(r[6]); close_stamp=close_stamp//1000 if close_stamp>10**14 else close_stamp
                    if close_stamp<end.timestamp()*1000:
                        rows.append([pd.to_datetime(stamp,unit='ms',utc=True),*map(float,r[1:5])])
                start=int(page[-1][0])+86400000
                if len(page)<1000: break
        else:
          try:
            for year in range(1999,end.year+1):
                if year==end.year and not (ROOT/'raw'/'gold'/f'{year}.bi5').exists():
                    chunks=[(ROOT/'raw'/'gold'/f'{year}-{m:02d}-hour.bi5',pd.Timestamp(year=year,month=m+1,day=1,tz='UTC'),f'https://datafeed.dukascopy.com/datafeed/XAUUSD/{year}/{m:02d}/BID_candles_hour_1.bi5?x=5',True) for m in range(end.month-1)]
                else:
                    chunks=[(ROOT/'raw'/'gold'/f'{year}.bi5',pd.Timestamp(f'{year}-01-01',tz='UTC'),f'https://datafeed.dukascopy.com/datafeed/XAUUSD/{year}/BID_candles_day_1.bi5?x=5',False)]
                hourrows=[]
                for path,anchor,url,hourly in chunks:
                    b=fetch(url,path,offline)
                    manifest['files'].append({'path':str(path.relative_to(ROOT)),'url':url,'sha256':hashlib.sha256(b).hexdigest()})
                    decoded=lzma.decompress(b); assert len(decoded)%24==0
                    for sec,o,c,l,h,v in struct.iter_unpack('>5if',decoded):
                        stamp=anchor+pd.Timedelta(seconds=sec)
                        if stamp+pd.Timedelta(hours=1 if hourly else 24)<=end and v>0:
                            if min(o,c,l,h)>0 and l<=min(o,c) and h>=max(o,c):
                                (hourrows if hourly else rows).append([stamp,o/1000,h/1000,l/1000,c/1000])
                            else: quarantine.append({'asset':'XAU','date':str(stamp),'open':o,'close':c,'low':l,'high':h})
                if hourrows:
                    hourlydf=pd.DataFrame(hourrows,columns=['date','open','high','low','close']).set_index('date').sort_index()
                    d=hourlydf.resample('1D').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
                    rows.extend([[date,*row] for date,row in d.iterrows()])
          except (OSError, TimeoutError) as exc:
            manifest['missing_assets'].append({'asset':asset,'reason':f'{type(exc).__name__}: 黄金数据源暂时不可用'})
            print(asset,'source unavailable:',type(exc).__name__,flush=True)
            continue
        df=pd.DataFrame(rows,columns=['date','open','high','low','close']).set_index('date').sort_index()
        assert not df.empty and not df.index.duplicated().any()
        assert (df.low>0).all() and (df.high>=df[['open','close','low']].max(axis=1)).all() and (df.low<=df[['open','close']].min(axis=1)).all()
        gaps=df.index.to_series().diff().dt.days
        if asset!='XAU': assert (gaps.dropna()==1).all(),'crypto gaps'
        manifest['coverage'].append({'asset':asset,'start':str(df.index.min().date()),'end':str(df.index.max().date()),'bars':len(df),'max_calendar_gap_days':int(gaps.max())})
        df.attrs['invalid_dates']=[pd.Timestamp(q['date']) for q in quarantine] if asset=='XAU' else []
        datasets[asset]=df
        (ROOT/'data').mkdir(exist_ok=True); df.to_csv(ROOT/'data'/f'{asset}_daily.csv')
        print(asset,manifest['coverage'][-1],flush=True)
    if not datasets:
        raise ValueError('No asset data collected')
    pd.DataFrame(quarantine,columns=['asset','date','open','close','low','high']).to_csv(ROOT/'data'/'quarantine.csv',index=False)
    manifest['quarantined_rows']=len(quarantine)
    manifest['xau_policy']='Completed year daily BID; completed current-year month hourly BID, aggregated UTC. Current month excluded; daily tracker has explicit source lag.'
    (ROOT/'data'/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return datasets

def aggregate(df,scale,end):
    if scale=='D': out=df.copy(); out['available']=out.index+pd.Timedelta(days=1); return out
    # UTC Monday-Sunday weeks and calendar months, stamped at period's start.
    rule='W-SUN' if scale=='W' else 'ME'
    out=df.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    out['available']=out.index+pd.Timedelta(days=1)
    coverage_end=min(end,df.index.max()+pd.Timedelta(days=1))
    out=out[out.available<=coverage_end]
    start_dates=out.index-pd.to_timedelta(out.index.weekday,unit='D') if scale=='W' else out.index.tz_localize(None).to_period('M').to_timestamp().tz_localize('UTC')
    out=out[start_dates>=df.index.min()]
    for stamp in df.attrs.get('invalid_dates',[]):
        label=stamp.normalize()+pd.Timedelta(days=6-stamp.weekday()) if scale=='W' else stamp.normalize()+pd.offsets.MonthEnd(0)
        if label in out.index: out.loc[label,['open','high','low','close']]=np.nan
    return out

def features(b,lookback):
    f=b.copy(); prev=f.close.shift(1)
    tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    f['atr']=tr.rolling(14,min_periods=14).mean().shift(1)
    f['level']=f.high.rolling(lookback,min_periods=lookback).max().shift(1)
    f['breakout']=(f.close>f.level+0.1*f.atr)&(f.close.shift(1)<=f.level.shift(1)+0.1*f.atr.shift(1))
    return f

def outcome(b,j,h,atr):
    if j+1+h>len(b): return None
    future=b.iloc[j+1:j+1+h]
    if future[['open','high','low','close']].isna().any().any(): return None
    entry=float(future.iloc[0].open)
    closes=np.r_[entry,future.close.to_numpy()]; peaks=np.maximum.accumulate(closes)
    dd=float(np.max((peaks-closes)/peaks))
    dd_atr=float(np.max(peaks-closes)/atr)
    return {'return_pct':float(future.iloc[-1].close/entry-1)*100,'return_atr':float((future.iloc[-1].close-entry)/atr),'max_dd_pct':dd*100,'max_dd_atr':dd_atr,'adverse_low_atr':float(max(0,entry-future.low.min())/atr)}

def research(datasets,end,lookback=20,root=None):
    ROOT=Path(root) if root is not None else Path(__file__).resolve().parents[2]/"data"/"breakout_acceptance"
    events=[]; tracks=[]; results=[]
    for asset,daily in datasets.items():
        weekly=features(aggregate(daily,'W',end),20)
        for scale in (('D','W') if asset!='XAU' else ('W','M')):
            b=features(aggregate(daily,scale,end),lookback if scale!='M' else 12)
            lag={'D':5,'W':2,'M':1}[scale]; cooldown={'D':20,'W':4,'M':2}[scale]
            horizons={'D':(20,60),'W':(4,12),'M':(3,6)}[scale]
            last=-10000
            for i in range(len(b)):
                if not b.iloc[i].breakout or i-last<cooldown: continue
                last=i; e=b.iloc[i]; level=float(e.level); atr=float(e.atr)
                eid=f'{asset}-{scale}-{e.available.date()}'
                known=e.available
                histw=weekly[weekly.available<=known]
                wconfirmed=bool(len(histw) and histw.iloc[-1].close>histw.iloc[-1].level)
                ev={'event_id':eid,'asset':asset,'scale':scale,'known_at_utc':str(known),'breakout_close':float(e.close),'level':level,'atr_frozen':atr,'weekly_confirmed_at_start':wconfirmed,'status':'pending'}
                # Daily observations after the event is known; no retrospectively shifted swing pivots.
                days=daily[(daily.index>=known)&(daily.index<known+pd.Timedelta(days=180))]
                closes=[]; lows=[]; running_high=float(e.high)
                for day,r in days.iterrows():
                    closes.append(float(r.close)); lows.append(float(r.low))
                    tracks.append({'event_id':eid,'date':str(day.date()),'elapsed_calendar_days':(day-known).days+1,'close_hold_fraction':float(np.mean(np.array(closes)>=level)),'close_distance_atr':float((r.close-level)/atr),'running_pullback_bottom':min(lows),'bottom_distance_atr':(min(lows)-level)/atr,'new_high':bool(r.high>running_high),'new_closing_high_over_breakout_high':bool(r.close>e.high)})
                    running_high=max(running_high,float(r.high))
                j=i+lag
                if j<len(b):
                    obs=b.iloc[i+1:j+1]
                    if obs[['open','high','low','close']].isna().any().any():
                        ev['status']='data_gap';events.append(ev);continue
                    hold=float((obs.close>=level).mean()); bottom=float((obs.low.min()-level)/atr)
                    newhigh=bool((obs.close>e.high).any())
                    accepted=hold>=0.8 and bottom>=-0.5 and newhigh
                    ev.update(status='accepted' if accepted else 'not_accepted',classification_known_at_utc=str(b.iloc[j].available),hold_fraction=hold,bottom_atr=bottom,new_closing_high=newhigh)
                    # Match non-breakouts by same calendar year, trailing volatility, weekly trend, past only.
                    pool=[]
                    for k in range(max(20,j-365 if scale=='D' else j-52),j-lag):
                        c=b.iloc[k]
                        if c.breakout or pd.isna(c.atr) or c.available.year!=b.iloc[j].available.year: continue
                        if k+1+max(horizons)>j: continue # fully mature when selecting control
                        wh=weekly[weekly.available<=c.available]
                        wc=bool(len(wh) and wh.iloc[-1].close>wh.iloc[-1].level)
                        if wc!=wconfirmed: continue
                        pool.append((abs(float(c.atr/c.close-e.atr/e.close)),k))
                    control=min(pool)[1] if pool else None
                    for h in horizons:
                        out=outcome(b,j,h,atr)
                        if out is None: continue
                        row={'event_id':eid,'asset':asset,'scale':scale,'horizon_bars':h,'accepted':accepted,'weekly_confirmed':wconfirmed,'split':'holdout' if b.iloc[j].available.year>=2023 else 'development','classification_at':str(b.iloc[j].available),**out}
                        if control is not None:
                            co=outcome(b,control,h,float(b.iloc[control].atr))
                            if co is None: results.append(row);continue
                            row.update(control_known_at=str(b.iloc[control].available),control_return_atr=co['return_atr'],control_dd_atr=co['max_dd_atr'],excess_return_atr=out['return_atr']-co['return_atr'])
                        results.append(row)
                events.append(ev)
    dest=ROOT/'reports';dest.mkdir(exist_ok=True)
    for name,rows in [('events',events),('daily_observations',tracks),('outcomes',results)]:pd.DataFrame(rows).to_csv(dest/f'{name}.csv',index=False)
    r=pd.DataFrame(results)
    summary=r.groupby(['asset','scale','horizon_bars','split','accepted']).agg(n=('return_atr','size'),median_return_atr=('return_atr','median'),median_dd_atr=('max_dd_atr','median'),median_return_pct=('return_pct','median'),median_dd_pct=('max_dd_pct','median'),matched_n=('excess_return_atr','count'),median_excess_atr=('excess_return_atr','median')).reset_index()
    summary.to_csv(dest/'summary.csv',index=False)
    review=r.groupby(['asset','scale','horizon_bars','weekly_confirmed']).agg(n=('return_atr','size'),median_return_atr=('return_atr','median'),median_dd_atr=('max_dd_atr','median')).reset_index();review.to_csv(dest/'weekly_review.csv',index=False)
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--end',default=dt.datetime.now(UTC).date().isoformat());p.add_argument('--offline',action='store_true');p.add_argument('--lookback',type=int,default=20)
    a=p.parse_args();end=pd.Timestamp(a.end,tz='UTC');data=collect(end,a.offline);s=research(data,end,a.lookback);print(s.to_string(index=False))
if __name__=='__main__':main()
