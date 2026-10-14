(() => {
  'use strict';
  const root = document.getElementById('breakout-chart-page');
  if (!root) return;
  const data = JSON.parse(document.getElementById('bc-chart-data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const color = {blue:'#2d7aaf', orange:'#d19a51', green:'#31a18b', muted:'#8aa1b1', grid:'#e9eff4', ink:'#6d8395'};
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = value => finite(value) ? new Intl.NumberFormat('zh-CN',{maximumFractionDigits:3}).format(value) : '—';
  const percent = value => finite(value) ? `${(value*100).toFixed(1)}%` : '—';
  const day = value => String(value || '').slice(0,10);
  const unit = data.scale === 'D' ? '日 K' : data.scale === 'W' ? '周 K' : '月 K';
  const node = (tag, attrs={}, text) => {
    const el = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key,value]) => el.setAttribute(key,String(value)));
    if (text !== undefined) el.textContent = text;
    return el;
  };
  let selected = data.selected, priceStyle = 'close', focus = false, requestNumber = 0;
  const select = document.getElementById('bc-event-select');
  const split = document.getElementById('bc-split');
  const horizon = document.getElementById('bc-horizon');
  const chartIds = ['bc-price','bc-path','bc-retention','bc-outcomes','bc-compare-return','bc-compare-dd'];
  const reset = id => {
    const wrap = document.getElementById(id), svg = wrap.querySelector('svg');
    svg.replaceChildren(); wrap.querySelector('.bc-tooltip').hidden = true;
    const w = Math.max(280,wrap.clientWidth), h = wrap.clientHeight;
    svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
    svg.onpointermove = null; svg.onpointerleave = null;
    return {wrap,svg,w,h};
  };
  const empty = (id,message) => {
    const f = reset(id);
    f.svg.append(node('text',{x:f.w/2,y:f.h/2,'text-anchor':'middle',fill:color.muted,'font-size':13},message));
  };
  function frame(id, xValues, yValues, labels, options={}) {
    const f = reset(id);
    const left = 56, right = 18, top = 17, bottom = 31;
    const values = yValues.filter(finite);
    let low = options.min ?? Math.min(...values), high = options.max ?? Math.max(...values);
    if (!Number.isFinite(low) || !Number.isFinite(high)) {low=0;high=1;}
    const pad = (high-low || Math.max(Math.abs(high)*.1,1))*.12;
    if (options.min === undefined) low -= pad;
    if (options.max === undefined) high += pad;
    if (high===low) high=low+1;
    const xMin = xValues[0] ?? 0, xMax = xValues[xValues.length-1] ?? 1;
    f.y = value => top + (high-value)/(high-low)*(f.h-top-bottom);
    f.x = index => left + ((xValues[index] ?? 0)-xMin)/(xMax-xMin || 1)*(f.w-left-right);
    f.plotLeft=left; f.plotRight=f.w-right; f.plotTop=top; f.plotBottom=f.h-bottom;
    f.low=low; f.high=high; f.xValues=xValues;
    for (let tick=0;tick<=4;tick++) {
      const value = low+(high-low)*tick/4, y=f.y(value);
      f.svg.append(node('line',{x1:left,x2:f.w-right,y1:y,y2:y,stroke:color.grid}));
      f.svg.append(node('text',{x:left-9,y:y+4,'text-anchor':'end',fill:color.ink,'font-size':10}, options.format ? options.format(value) : number(value)));
    }
    const used = new Set();
    for (let tick=0;tick<=4;tick++) {
      const i=Math.round((xValues.length-1)*tick/4);
      if (i<0 || used.has(i)) continue;
      used.add(i);
      f.svg.append(node('text',{x:f.x(i),y:f.h-7,'text-anchor':'middle',fill:color.ink,'font-size':10},labels[i] ?? ''));
    }
    return f;
  }
  function reference(f,value,label,stroke=color.muted) {
    if (value<f.low || value>f.high) return;
    const y=f.y(value);
    f.svg.append(node('line',{x1:f.plotLeft,x2:f.plotRight,y1:y,y2:y,stroke,'stroke-dasharray':'5 5','stroke-width':1}));
    if (label) f.svg.append(node('text',{x:f.plotRight-4,y:y-5,'text-anchor':'end',fill:stroke,'font-size':10},label));
  }
  function line(f,values,stroke,width=2) {
    let d='', open=false;
    values.forEach((value,i) => {
      if (!finite(value)) {open=false;return;}
      d+=`${open?'L':'M'}${f.x(i).toFixed(2)},${f.y(value).toFixed(2)} `; open=true;
    });
    f.svg.append(node('path',{d,fill:'none',stroke,'stroke-width':width,'stroke-linejoin':'round','stroke-linecap':'round'}));
  }
  function hover(f,rows,labelFor) {
    const cursor=node('line',{y1:f.plotTop,y2:f.plotBottom,stroke:'#b8c7d2','stroke-dasharray':'3 4','pointer-events':'none',visibility:'hidden'});
    f.svg.append(cursor);
    const tooltip=f.wrap.querySelector('.bc-tooltip');
    f.svg.onpointermove = event => {
      const rect=f.svg.getBoundingClientRect(), px=(event.clientX-rect.left)/rect.width*f.w;
      if (px<f.plotLeft || px>f.plotRight) {tooltip.hidden=true;cursor.setAttribute('visibility','hidden');return;}
      let closest=0, distance=Infinity;
      rows.forEach((_,i) => {const d=Math.abs(f.x(i)-px);if(d<distance){distance=d;closest=i;}});
      tooltip.textContent=labelFor(rows[closest],closest);tooltip.hidden=false;
      const localX=event.clientX-f.wrap.getBoundingClientRect().left;
      tooltip.style.left=`${Math.max(4,Math.min(f.wrap.clientWidth-tooltip.offsetWidth-4,localX+15))}px`;
      tooltip.style.top='12px';
      cursor.setAttribute('x1',f.x(closest));cursor.setAttribute('x2',f.x(closest));cursor.setAttribute('visibility','visible');
    };
    f.svg.onpointerleave=()=>{tooltip.hidden=true;cursor.setAttribute('visibility','hidden');};
  }
  function drawPrice() {
    let rows=data.candles;
    if (focus && selected) {
      const start=Date.parse(selected.known_at)-30*86400000, end=Date.parse(selected.known_at)+180*86400000;
      rows=rows.filter(row=>Date.parse(row.date)>=start && Date.parse(row.date)<=end);
    }
    if (!rows.length) {empty('bc-price','当前范围暂无完整日 K');return;}
    const candles=priceStyle==='candles' && rows.length<=800;
    const values=candles?rows.flatMap(row=>[row.low,row.high]):rows.map(row=>row.close);
    if (selected && finite(selected.facts.level)) values.push(selected.facts.level);
    const f=frame('bc-price',rows.map(row=>Date.parse(row.date)),values,rows.map(row=>row.date));
    if (candles) {
      const width=Math.max(1,Math.min(9,(f.plotRight-f.plotLeft)/rows.length*.65));
      rows.forEach((row,i)=>{
        const x=f.x(i), c=row.close>=row.open?color.blue:color.orange;
        f.svg.append(node('line',{x1:x,x2:x,y1:f.y(row.high),y2:f.y(row.low),stroke:c,'stroke-width':1}));
        f.svg.append(node('rect',{x:x-width/2,y:Math.min(f.y(row.open),f.y(row.close)),width,height:Math.max(1,Math.abs(f.y(row.open)-f.y(row.close))),fill:c}));
      });
    } else line(f,rows.map(row=>row.close),color.blue,1.7);
    if (selected && finite(selected.facts.level)) reference(f,selected.facts.level,`突破水平 ${number(selected.facts.level)}`);
    hover(f,rows,row=>`${row.date} · UTC\n开 ${number(row.open)}  高 ${number(row.high)}\n低 ${number(row.low)}  收 ${number(row.close)}`);
    for (const event of data.events) {
      const stamp=Date.parse(event.known_at)-86400000;
      if(stamp<Date.parse(rows[0].date) || stamp>Date.parse(rows[rows.length-1].date))continue;
      let i=0;
      for(let j=0;j<rows.length;j++){if(Date.parse(rows[j].date)<=stamp)i=j;else break;}
      const active=selected?.id===event.id;
      const point=node('circle',{cx:f.x(i),cy:f.y(rows[i].close),r:active?6:4.5,fill:active?'#173c56':'#7394ad',stroke:'#fff','stroke-width':2,tabindex:0,role:'button','aria-label':`${day(event.known_at)} 突破起点，查看事件`,class:'bc-chart-event'});
      point.append(node('title',{},`${day(event.known_at)} · 点击查看突破后路径`));
      point.addEventListener('click',()=>loadEvent(event.id));
      point.addEventListener('keydown',e=>{if(e.key==='Enter' || e.key===' '){e.preventDefault();loadEvent(event.id);}});
      f.svg.append(point);
    }
  }
  function markConfirmation(f, rows) {
    if (!selected?.facts.classification_known_at_utc || !rows.length) return;
    const elapsed = (Date.parse(selected.facts.classification_known_at_utc)-Date.parse(selected.known_at))/86400000;
    const i = rows.findLastIndex(row => row.elapsed_calendar_days <= elapsed);
    if (i < 0) return;
    const x = f.x(i);
    const shade = node('rect',{x:f.plotLeft,y:f.plotTop,width:Math.max(0,x-f.plotLeft),height:f.plotBottom-f.plotTop,fill:'#eef4f7','fill-opacity':.7});
    f.svg.insertBefore(shade,f.svg.firstChild);
    f.svg.append(node('line',{x1:x,x2:x,y1:f.plotTop,y2:f.plotBottom,stroke:'#9cafbc','stroke-dasharray':'3 4'}));
    f.svg.append(node('text',{x:Math.min(f.plotRight-80,x+5),y:f.plotTop+10,fill:'#7b92a3','font-size':9},'确认窗口结束'));
  }
  function drawPaths() {
    const rows=selected?.observations || [];
    if (!rows.length) {
      empty('bc-path','此事件尚无已完成的突破后日线');empty('bc-retention','尚无收盘保持记录');return;
    }
    const x=rows.map(row=>row.elapsed_calendar_days), labels=rows.map(row=>`第 ${row.elapsed_calendar_days} 天`);
    const f=frame('bc-path',x,rows.flatMap(row=>[row.close_distance_atr,row.bottom_distance_atr]).concat([0,-.5]),labels);
    markConfirmation(f,rows);
    reference(f,0,'');reference(f,-.5,'−0.5 ATR',color.orange);
    line(f,rows.map(row=>row.close_distance_atr),color.blue);
    line(f,rows.map(row=>row.bottom_distance_atr),color.orange,1.8);
    rows.forEach((row,i)=>{if(row.new_high && finite(row.close_distance_atr))f.svg.append(node('circle',{cx:f.x(i),cy:f.y(row.close_distance_atr),r:3.5,fill:color.green,stroke:'#fff','stroke-width':1}));});
    hover(f,rows,row=>`${row.date} · 第 ${row.elapsed_calendar_days} 天\n收盘距水平 ${number(row.close_distance_atr)} ATR\n运行底部 ${number(row.running_pullback_bottom)}\n底部距水平 ${number(row.bottom_distance_atr)} ATR\n再次创新高：${row.new_high?'是':'否'}`);
    const retention=frame('bc-retention',x,rows.map(row=>row.close_hold_fraction),labels,{min:0,max:1,format:percent});
    markConfirmation(retention,rows);
    reference(retention,.8,'80% 观察门槛',color.orange);
    line(retention,rows.map(row=>row.close_hold_fraction),color.blue,2.2);
    hover(retention,rows,row=>`${row.date} · 第 ${row.elapsed_calendar_days} 天\n累计收盘保持率 ${percent(row.close_hold_fraction)}`);
  }
  function bars(id,groups,series) {
    if(!groups.length || !groups.some(g=>series.some(s=>finite(g[s.key])))) {empty(id,'暂无已成熟的结果样本');return;}
    const vals=groups.flatMap(g=>series.map(s=>g[s.key])).filter(finite).concat(0);
    const f=frame(id,groups.map((_,i)=>i),vals,[]);
    reference(f,0,'');
    const slot=(f.plotRight-f.plotLeft)/groups.length, total=Math.min(slot*.58,160), width=total/series.length;
    groups.forEach((group,i)=>{
      const center=f.plotLeft+slot*(i+.5);
      series.forEach((s,k)=>{
        const value=group[s.key]; if(!finite(value))return;
        const x=center-total/2+k*width+4, y=Math.min(f.y(value),f.y(0));
        const bar=node('rect',{x,y,width:Math.max(3,width-8),height:Math.max(1,Math.abs(f.y(value)-f.y(0))),fill:s.color,rx:3});
        bar.append(node('title',{},`${group.label}\n${s.name}: ${number(value)} ATR${group.n!==undefined?`\n样本数 ${group.n}`:''}`));f.svg.append(bar);
        f.svg.append(node('text',{x:x+(width-8)/2,y:value>=0?y-6:f.y(value)+14,'text-anchor':'middle',fill:s.color,'font-size':11},number(value)));
      });
      f.svg.append(node('text',{x:center,y:f.h-8,'text-anchor':'middle',fill:color.ink,'font-size':11},group.label));
    });
  }
  function drawOutcomes() {
    const rows=selected?.outcomes || [];
    bars('bc-outcomes',rows.map(row=>({...row,label:`后续 ${row.horizon_bars} 根${unit}`})),[{key:'return_atr',name:'后续收益',color:color.blue},{key:'max_dd_atr',name:'最大回撤',color:color.orange}]);
  }
  function metrics() {
    const rows=selected?.observations || [], last=rows[rows.length-1];
    document.getElementById('bc-status').textContent=selected?.status_label || '尚无事件';
    document.getElementById('bc-known').textContent=selected?.facts.classification_known_at_utc ? `确认于 ${day(selected.facts.classification_known_at_utc)} UTC` : '观察窗口尚未完成';
    document.getElementById('bc-hold').textContent=percent(last?.close_hold_fraction);
    document.getElementById('bc-bottom').textContent=number(last?.running_pullback_bottom);
    document.getElementById('bc-bottom-distance').textContent=last?`${number(last.bottom_distance_atr)} ATR · ${last.date}`:'相对突破水平 / ATR';
    document.getElementById('bc-new-high').textContent=rows.length?`${rows.filter(row=>row.new_high).length} 次`:'—';
    document.getElementById('bc-event-description').textContent=selected?`${unit} · 起点 ${day(selected.known_at)} UTC · ${rows.length} 条逐日观察${last?` · 观察至 ${last.date}`:''}`:'当前范围没有事件，可扩大价格范围。';
    const detail=document.getElementById('bc-detail-link');detail.hidden=!selected;
    if(selected)detail.href=root.dataset.detailUrl.replace('/0/',`/${selected.id}/`);
  }
  function drawComparison() {
    const rows=data.summary.filter(row=>row.split===split.value && Number(row.horizon_bars)===Number(horizon.value));
    const groups=[true,false].map(accepted=>{
      const row=rows.find(row=>row.accepted===accepted);
      return {label:accepted?'接受':'未确认接受',n:row?.n || 0,return:row?.median_return_atr,dd:row?.median_dd_atr};
    });
    bars('bc-compare-return',groups,[{key:'return',name:'收益中位数',color:color.blue}]);
    bars('bc-compare-dd',groups,[{key:'dd',name:'回撤中位数',color:color.orange}]);
    const note=document.getElementById('bc-sample-note');
    note.textContent=`接受组 ${groups[0].n} 例 · 未确认接受组 ${groups[1].n} 例${Math.min(...groups.map(g=>g.n))<8?'。样本偏少，差异仅作描述，不据此判断稳定优势。':'。注意事件窗口可能重叠，收益提高与回撤降低需要分别检验。'}`;
  }
  function render() {drawPrice();drawPaths();drawOutcomes();metrics();drawComparison();}
  async function loadEvent(id) {
    if(!id)return;
    const ticket=++requestNumber;
    document.getElementById('bc-event-description').textContent='正在载入所选事件…';
    try {
      const response=await fetch(`${root.dataset.eventUrl.replace('/0/',`/${id}/`)}?asset=${encodeURIComponent(data.asset)}`,{headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('研究快照可能已更新，请刷新页面后重选事件。');
      const next=await response.json();if(ticket!==requestNumber)return;
      selected=next; select.value=String(next.id);
      const url=new URL(window.location.href);url.searchParams.set('event',String(next.id));history.replaceState(null,'',url);
      render();
    } catch(error) {
      if(ticket!==requestNumber)return;
      document.getElementById('bc-event-description').textContent=`载入失败：${error.message}`;
    }
  }
  const windows=[...new Set(data.summary.map(row=>Number(row.horizon_bars)))].sort((a,b)=>a-b);
  for(const value of windows){const option=document.createElement('option');option.value=String(value);option.textContent=`${value} 根${unit}`;horizon.append(option);}
  select.addEventListener('change',()=>loadEvent(select.value));
  split.addEventListener('change',drawComparison);horizon.addEventListener('change',drawComparison);
  root.querySelectorAll('[data-price-style]').forEach(button=>button.addEventListener('click',()=>{
    priceStyle=button.dataset.priceStyle;root.querySelectorAll('[data-price-style]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));drawPrice();
  }));
  document.getElementById('bc-focus').addEventListener('click',event=>{focus=!focus;event.currentTarget.setAttribute('aria-pressed',String(focus));drawPrice();});
  let resizeFrame;
  new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(render);}).observe(root);
  render();
})();
