from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from datetime import timedelta
from .models import ASSETS, DailyCandle, ResearchEvent, ResearchRun
from .services import get_schedule, queue_run, storage_root
from apps.scheduling.services import calculate_next_run_at, scheduler_status


def selected_asset(request):
    asset=request.GET.get('asset','SOL')
    if asset not in ASSETS: raise Http404('Unknown research asset')
    return asset

@require_GET
def index(request):
    asset=selected_asset(request)
    latest=ResearchRun.objects.filter(status__in=['success','partial']).first()
    schedule=get_schedule()
    executor=scheduler_status()
    online=executor['online'] or bool(schedule.worker_heartbeat_at and schedule.worker_heartbeat_at>=timezone.now()-timedelta(seconds=90))
    rows=ResearchEvent.objects.filter(run=latest,asset=asset) if latest else ResearchEvent.objects.none()
    scale=request.GET.get('scale','')
    if scale and scale not in ('D','W','M'):raise Http404('Invalid scale')
    if scale:rows=rows.filter(scale=scale)
    page=Paginator(rows,25).get_page(request.GET.get('page'))
    return render(request,'breakout_acceptance/index.html',{'asset':asset,'assets':ASSETS,'latest':latest,'schedule':schedule,'online':online,'page':page,'scale':scale,'runs':ResearchRun.objects.all()[:10],'summary':[x for x in latest.payload.get('summary',[]) if x['asset']==asset] if latest else []})

@require_POST
def configure(request):
    schedule=get_schedule()
    if request.POST.get('action')=='collect':
        run,created=queue_run()
        messages.success(request,'已加入研究队列，由程序后台执行。' if created else '已有等待或运行中的研究任务，请勿重复提交。')
    elif request.POST.get('action') in ('enable','disable'):
        schedule.enabled=request.POST['action']=='enable'
        schedule.next_run_at=calculate_next_run_at(schedule.run_time)
        schedule.save(update_fields=['enabled','next_run_at'])
        messages.success(request,'每日 08:20 采集已启用。' if schedule.enabled else '每日自动采集已暂停；现有运行会完成。')
    else: raise Http404('Unknown action')
    return redirect('breakout_acceptance:index')

@require_GET
def data(request):
    asset=selected_asset(request)
    qs=DailyCandle.objects.filter(asset=asset)
    page=Paginator(qs,100).get_page(request.GET.get('page'))
    return render(request,'breakout_acceptance/data.html',{'asset':asset,'assets':ASSETS,'page':page,'count':qs.count()})

@require_GET
def event(request,pk):
    e=get_object_or_404(ResearchEvent,pk=pk)
    return render(request,'breakout_acceptance/event.html',{'event':e})

@require_GET
def run(request,pk):
    r=get_object_or_404(ResearchRun,pk=pk)
    return render(request,'breakout_acceptance/run.html',{'run':r})

@require_GET
def download(request,pk,kind):
    r=get_object_or_404(ResearchRun,pk=pk,status__in=['success','partial'])
    files={'report':'reports/report.html','events':'reports/events.csv','observations':'reports/daily_observations.csv','outcomes':'reports/outcomes.csv','summary':'reports/summary.csv','manifest':'data/manifest.json','quarantine':'data/quarantine.csv'}
    if kind not in files:raise Http404()
    path=storage_root()/'runs'/str(r.pk)/files[kind]
    if not path.is_file():raise Http404()
    return FileResponse(path.open('rb'),as_attachment=True,filename=path.name)
