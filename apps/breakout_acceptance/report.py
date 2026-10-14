import json,html,hashlib
from pathlib import Path
import numpy as np
import pandas as pd
from .engine import ROOT

def table(df): return df.to_html(index=False,border=0,float_format=lambda x:f'{x:.3f}',na_rep='—')
def build(root=None):
 ROOT=Path(root) if root is not None else Path(__file__).resolve().parents[2]/"data"/"breakout_acceptance"
 m=json.loads((ROOT/'data/manifest.json').read_text());s=pd.read_csv(ROOT/'reports/summary.csv');r=pd.read_csv(ROOT/'reports/outcomes.csv');e=pd.read_csv(ROOT/'reports/events.csv')
 rng=np.random.default_rng(20261014);ci=[]
 r['year']=pd.to_datetime(r.classification_at).dt.year
 for keys,g in r.groupby(['asset','scale','horizon_bars','split']):
  a=g[g.accepted];b=g[~g.accepted];years=g.year.unique()
  for metric in ['return_atr','max_dd_atr']:
   delta=float(a[metric].median()-b[metric].median()) if len(a) and len(b) else np.nan
   samples=[]
   if len(years)>=4 and min(len(a),len(b))>=8:
    for _ in range(1000):
     chosen=rng.choice(years,size=len(years),replace=True)
     x=pd.concat([g[g.year==y] for y in chosen]);aa=x[x.accepted];bb=x[~x.accepted]
     if len(aa) and len(bb): samples.append(aa[metric].median()-bb[metric].median())
   lo,hi=np.quantile(samples,[.025,.975]) if samples else (np.nan,np.nan)
   ci.append(dict(zip(['asset','scale','horizon_bars','split'],keys))|{'metric':metric,'accepted_minus_other':delta,'year_cluster_ci_low':lo,'year_cluster_ci_high':hi,'accepted_n':len(a),'other_n':len(b),'years':len(years)})
 ci=pd.DataFrame(ci);ci.to_csv(ROOT/'reports/cluster_intervals.csv',index=False)
 headline=s[(s.split=='holdout')&(((s.scale=='D')&(s.horizon_bars==20))|((s.asset=='XAU')&(s.scale=='W')&(s.horizon_bars==12)))]
 q=pd.read_csv(ROOT/'data/quarantine.csv')
 coverage=pd.DataFrame(m['coverage']);coverage['lag_calendar_days']=[(pd.Timestamp(m['end_exclusive_utc'])-pd.Timestamp(x,tz='UTC')).days-1 for x in coverage.end]
 quality=q.assign(year=pd.to_datetime(q.date).dt.year).groupby('year').size().rename('quarantined').reset_index()
 status=e.groupby(['asset','scale','status']).size().rename('n').reset_index()
 sources='<a href="https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints">Binance 现货 API</a>；<a href="https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data">Binance USDⓈ-M 合约 K 线 API</a>'
 text=f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>突破后价格重心是否被接受 — 第二版</title><style>body{{font:16px/1.65 system-ui;margin:40px auto;max-width:1250px;color:#233041;padding:0 25px}}h1,h2{{color:#164b57}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:8px;border-bottom:1px solid #dce5e9;text-align:right}}th{{background:#edf4f5}}.note{{background:#fff4dc;padding:16px}}section{{overflow:auto}}a{{color:#086a85}}</style>
 <h1>突破后价格重心是否被接受</h1><p>第一阶段：固定资产事件研究，无真实交易接口。截止日（不含）{html.escape(m['end_exclusive_utc'])}。由 Market Evidence Lab 程序采集、调度与展示。</p>
 <h2>结论</h2><p>接受状态并不自动意味着收益更高且回撤更低。请逐资产查看本轮统计与区间，分别判断收益与回撤；小样本不足以确认稳定优势。接受程度是可检验的状态描述，历史时间切片不是前瞻验证。</p>
 <p class="note">表中的 holdout 是预先固定规则下的历史时间切片（2023 年及以后），不是前瞻真实样本外验证。没有在切片上优化参数。XAUUSDT 永续合约自上市后才有历史数据；月线样本可能不足。{m["quarantined_rows"]} 条不合 OHLC 的原始记录被隔离。{'本轮未取得：' + ', '.join(x['asset'] for x in m['missing_assets']) + '；这些资产不参与本轮统计。' if m['missing_assets'] else ''}</p>
 <h2>核实的数据范围</h2><section>{table(coverage)}</section><p>{sources}。SOLUSDT、BNBUSDT、ZECUSDT 为币安现货；XAUUSDT 为币安 USDⓈ-M 永续合约成交 K 线，均以 USDT 计价。XAUUSDT 合约价格不能等同于现货黄金 XAU/USD 买价，亦不能与旧版黄金结果直接拼接。</p>
 <h2>统一事件与无未来数据规则</h2><p>日/周突破：收盘首次穿越之前 20 根最高价 + 0.1×此前 14 根简单 ATR；月线使用之前 12 根最高价。最高价窗口和 ATR 均 shift(1)。只使用已完成 K。起点记录为突破 K 的结束时刻 known_at_utc。日/周/月冷却分别为 20/4/2 根，防止持续创新高每天被当成独立起点。冷却后仍须再次穿越，不能事后选取最低点。</p>
 <p>日/周/月确认窗口为后续 5/2/1 根；接受条件同时满足：至少 80% 收盘保持在突破水平上方，最低价不低于水平减 0.5 ATR，且至少一次收盘超过突破 K 最高价。各项也保留连续数值，而非仅保存二元标签。ATR 冻结于突破之前，不用未来波动率归一化。</p>
 <p>逐日轨迹最多追踪 180 个自然日，记录累计收盘保持率、收盘距突破水平 ATR、运行回撤底部及其 ATR 距离、是否再次创运行新高、是否收盘超过突破 K 最高价。周线复核只查询起点时已完成的周 K，条件为周收盘高于之前 20 周最高价。XAU 主研究为周/月突破，日线只做路径跟踪。</p>
 <p>后续收益从确认后下一根开盘开始，日线检验 20/60 根，周线 4/12 根，月线 3/6 根。最大回撤主口径为从入场开盘到每根收盘的运行峰值跌幅，同时输出最低价相对入场的最大不利变动；不假设一根 K 内高低价出现顺序。缺少完整后续窗口的事件右删失，不填零。污染的确认/结果窗口不进入统计。</p>
 <h2>2023 年以后历史对照</h2><p>return_atr：价格变化/突破前 ATR；dd_atr：收盘路径最大峰谷价差/同一 ATR。pct 列为百分数。n 为完成该结果窗口的事件数；接受与未接受不构成随机分组。</p><section>{table(headline)}</section>
 <h2>所有资产、尺度、期限</h2><section>{table(s)}</section>
 <h2>收益与回撤分别检验</h2><p>按事件确认年份进行 1000 次成组 bootstrap，随机种子 20261014。仅至少 4 个年份且两组各不少于 8 例时给出区间；其余显示空值，不以缺失区间表示显著。多个资产与期限未校正多重检验，结果为探索性统计。</p><section>{table(ci)}</section>
 <h2>同资产历史非突破对照</h2><p>从确认时点之前同年、已成熟的非突破日/周/月 K 中选取一个对照；周线状态一致，突破前 ATR/close 最接近。所有对照数据均来自本资产，结果窗口在当时已完成。matched_n 显示匹配数；excess_atr 为事件收益减对照收益。对照可能复用且不能排除市场状态差异、选择偏差或波动率均值回复，不作因果解释。对照样本稀疏时不推断稳定超额收益。</p>
 <h2>周线复核</h2><section>{table(pd.read_csv(ROOT/'reports/weekly_review.csv'))}</section>
 <h2>数据质量和事件计数</h2><section>{table(status)}{table(quality)}</section>
 <h2>复现与验证</h2><p>原始响应保留于 raw/，逐文件 SHA-256 记录于 data/manifest.json；数据、事件、逐日路径、结果、汇总均为 CSV。依赖版本冻结。运行 manage.py collect_breakout_research --offline --end 2026-10-14 可以离线复现。项目隔离测试验证前缀不变性、未来扰动不改变过去、未完成周月排除、手算收益回撤与右删失、历史高点窗口。报告不依赖实时网页内容。</p>
 <h2>局限</h2><p>均来自币安，但现货与永续合约的交易机制不同；USDT 与美元不是严格相同计价。资产和历史长度不齐，XAUUSDT 合约上市时间较晚；不能把不同 K 尺度的 ATR 直接当作相同持有期风险。ATR 归一化减少幅度差异，不消除分布尾部差异。事件冷却不足以使长窗口完全独立，年度 bootstrap 也无法充分处理所有重叠与短历史。使用 UTC 收盘，不是纽约收盘或黄金结算价。</p><p>不含手续费、买卖价差、滑点、资金费率及持仓约束；这是事件研究，非组合收益回测。接受定义含短期上涨条件，必须从确认之后计算收益以避免自证；不能排除动量或市场阶段解释。下一阶段应积累真正前瞻样本、做预声明参数敏感性及不重叠样本复核。</p></html>'''
 (ROOT/'reports/report.html').write_text(text,encoding='utf-8')
 (ROOT/'reports/update_status.json').write_text(json.dumps({'report_end':m['end_exclusive_utc'],'coverage':m['coverage'],'quarantined':m['quarantined_rows'],'scope':[x['asset'] for x in m['coverage']],'missing_assets':m['missing_assets']},indent=2))
 print('report.html generated; quarantine rows',len(q))
if __name__=='__main__':build()
