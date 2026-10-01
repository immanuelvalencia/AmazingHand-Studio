"""Sequential independent model runs belonging to one user launch."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .experiments import RUNS_ROOT, ROOT, RunStore
from .automated_study import run_complete_study


def run_session(dataset, protocol, models, repetitions, seed, settings, metadata,
                cancel, emit, motor_tolerance=1.0, tip_tolerance=2.0, physical=None,
                include_deterministic=True, parent=None):
    parent=Path(parent or RUNS_ROOT)
    jobs=([{'models':[], 'deterministic':True,'label':'Deterministic'}] if include_deterministic else [])
    jobs += [{'models':[m],'deterministic':False,'label':m} for m in dict.fromkeys(models)]
    if not jobs:raise ValueError('Select deterministic or at least one model.')
    session='S'+uuid4().hex[:8]
    record={'session_id':session,'created_utc':datetime.now(timezone.utc).isoformat(),
            'runs':[dict(job,status='pending') for job in jobs]}
    directory=parent/'sessions';directory.mkdir(parents=True,exist_ok=True)
    path=directory/(session+'.json')
    def save():
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(record,indent=2),encoding='utf-8');temp.replace(path)
    save()
    try:
        for index,job in enumerate(jobs):
            if cancel.is_set():break
            item=record['runs'][index]
            store=RunStore(dataset,settings,{**metadata,'models':job['models'],
                'include_deterministic':job['deterministic'],'session_id':session,
                'session_run_index':index+1,'session_run_count':len(jobs),
                'session_manifest':str(path.resolve()),'repetitions':repetitions,'seed':seed},parent=parent)
            item.update(path=str(store.path.resolve()),status='running');save()
            emit('run_store',store)
            prefix=f'{session} · run {index+1}/{len(jobs)} · {job["label"]}'
            def forward(kind,value):
                emit(kind, f'{prefix} · {value}' if kind=='status' else value)
            try:
                run_complete_study(dataset,protocol,job['models'],repetitions,seed,settings,store,cancel,
                    forward,motor_tolerance,tip_tolerance,physical=physical,
                    include_deterministic=job['deterministic'])
                result=json.loads((store.path/'study_completion.json').read_text(encoding='utf-8'))
                complete=result.get('matrix',{}).get('complete') and not result.get('error')
                item.update(status='complete' if complete else 'partial',export_errors=result.get('export_errors',{}))
                save()
                if not complete:break
            except Exception as exc:
                item.update(status='failed',error=str(exc));save();raise
    finally:
        for item in record['runs']:
            if item['status']=='pending':item['status']='not started'
        record['cancelled']=cancel.is_set()
        record['complete']=all(r['status']=='complete' for r in record['runs'])
        save()
    emit('status',f'{session}: {sum(r["status"]=="complete" for r in record["runs"])}/{len(jobs)} separate runs completed. Use Aggregate runs to compare them.')
    return path
