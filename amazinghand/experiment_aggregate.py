"""Read-only source-run aggregation with compatibility and duplicate checks."""
import hashlib
import json
from pathlib import Path
from .run_folders import create_run_folder, study_metadata
from .experiments import AGGREGATES_ROOT, ROOT, RunStore
from .experiment_report import export_evaluations, make_charts, write_report


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def discover_runs(folder):
    folder=Path(folder)
    return sorted(p.parent for p in folder.rglob('manifest.json')
                  if (p.parent/'trials.jsonl').exists() and not read(p).get('aggregation'))


def compatibility(manifest, dataset):
    meta=manifest.get('metadata',{})
    hashes={k.replace('\\','/'):v for k,v in manifest.get('source_hashes',{}).items()}
    return {'dataset':digest(sorted(dataset,key=lambda r:r['id'])),
            'backend':meta.get('execution_backend'),
            'reference_protocol':meta.get('reference_protocol_sha256'),
            'motor_tolerance':meta.get('motor_tolerance_deg'),
            'tip_tolerance':meta.get('tip_tolerance_mm'),
            'parser':manifest.get('parser_version'), 'scoring':manifest.get('scoring'),
            'semantics':{k:hashes.get(k) for k in ('amazinghand/chat/commands.py','amazinghand/chat/logic.py',
                'amazinghand/chat/chat_session.py','voice_hand/commands.py','voice_hand/logic.py',
                'voice_hand/chat_session.py','amazinghand/kinematics.py','amazinghand/gestures.py','amazinghand/experiment_protocol.py')}}


def aggregate_runs(paths, output_parent=None):
    paths=list(dict.fromkeys(Path(p).resolve() for p in paths))
    if not paths: raise ValueError('Select at least one run.')
    rows=[]; seen={}; sources=[]; base=None; inference=None; model_digests={}; warnings=[]
    duplicates=0; source_trials=set()
    for path in paths:
        manifest=read(path/'manifest.json'); dataset=read(path/'dataset.json')
        if manifest.get('aggregation'): raise ValueError('Select original runs, not an existing aggregate.')
        key=compatibility(manifest,dataset)
        if base is None: base=key; first=manifest; first_dataset=dataset
        differences=[k for k in key if key[k]!=base[k]]
        if differences: raise ValueError(f'{path.name}: incompatible '+', '.join(differences)+'. Select matching runs.')
        raw=(path/'trials.jsonl').read_bytes()
        raw_hash=hashlib.sha256(raw).hexdigest()
        if raw_hash in source_trials:
            duplicates+=len(raw.decode('utf-8').splitlines())
            continue
        source_trials.add(raw_hash)
        records=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        completion=read(path/'completion.json') if (path/'completion.json').exists() else {}
        if not completion.get('complete'): warnings.append(f'{path.name}: partial or completion not recorded; only recorded attempts included.')
        if any(r.get('algorithm') in ('llm','hybrid') for r in records):
            signature={k:manifest.get(k) for k in ('inference_version','prompt','schema','inference','timing')}
            signature['timeout']=manifest.get('settings',{}).get('timeout_seconds')
            hashes={k.replace('\\','/'):v for k,v in manifest.get('source_hashes',{}).items()}
            signature['implementation']={k:hashes.get(k) for k in ('amazinghand/fast_chat.py','amazinghand/command_inference.py')}
            if inference is not None and signature!=inference:
                raise ValueError(f'{path.name}: different inference contract/settings. Do not pool old and revised prompts.')
            inference=signature
            tags=read(path/'ollama_tags.json') if (path/'ollama_tags.json').exists() else {}
            tags={m['name']:m.get('digest') for m in tags.get('models',[])}
            for model in {r.get('model') for r in records if r.get('model')}:
                fingerprint=tags.get(model)
                if model in model_digests and model_digests[model]!=fingerprint:
                    raise ValueError(f'{model}: model digest differs between runs.')
                model_digests[model]=fingerprint
        sources.append({'path':str(path),'trials_sha256':raw_hash,
                        'manifest':manifest,'completion':completion,'recorded':len(records)})
        for row in records:
            if row.get('algorithm') not in ('deterministic','llm','hybrid'): continue
            identity=row['trial_id']; fingerprint=digest(row)
            if identity in seen:
                if seen[identity]!=fingerprint: raise ValueError(f'Conflicting trial ID: {identity}')
                duplicates+=1; continue
            seen[identity]=fingerprint
            row=dict(row,source_run=str(path),source_repetition=row.get('repetition',1))
            row['repetition']=f'{path}:{row["source_repetition"]}'
            rows.append(row)
    if not rows: raise ValueError('No recorded benchmark trials in selected runs.')
    metadata=study_metadata(first_dataset,first.get('metadata',{}))
    metadata.update(models=list(dict.fromkeys(r['model'] for r in rows if r.get('model'))),
                    include_deterministic=any(r['algorithm']=='deterministic' for r in rows),
                    repetitions='see source runs')
    output=create_run_folder(output_parent or AGGREGATES_ROOT,
                             metadata['models'],metadata['include_deterministic'],study=metadata['study_label'])
    store=RunStore.__new__(RunStore);store.path=output;store.rows=rows
    store.write('manifest.json',{**first,'aggregation':True,'metadata':metadata})
    store.write('dataset.json',first_dataset)
    for name in ('reference_execution_checks.json','persistence_checks.json'):
        checks=[]
        for source in sources:
            file=Path(source['path'])/name
            if file.exists():checks.extend(dict(r,source_run=source['path']) for r in read(file))
        store.write(name,checks)
    store.write('aggregation.json',{'sources':sources,'duplicates_skipped':duplicates,'warnings':warnings,
        'note':'Independent recorded trials are pooled with source identity; copied trial IDs count once. Captures remain in original folders.'})
    (output/'trials.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    store.write('completion.json',{'complete':all(s['completion'].get('complete',False) for s in sources),
                                 'scheduled':sum(s['completion'].get('scheduled',s['recorded']) for s in sources)})
    store.export();export_evaluations(store);make_charts(output);write_report(output)
    return output


def append_provenance(output):
    output=Path(output)
    aggregate=output/'aggregation.json'
    if not aggregate.exists():return
    data=read(aggregate);sources=data['sources'];duplicates=data['duplicates_skipped'];warnings=data['warnings']
    # Preserve the report's exact denominators and disclose pooling and source coverage.
    report=output/'report.md'
    provenance='\n## Aggregation provenance\n\nTrials are pooled across the selected runs. Repetition IDs retain their source; different runs are not treated as matched repetitions. Unequal sample sizes are shown in the tables. This is a cross-session descriptive comparison; machine/load/order differences can affect latency. Captures remain in their source folders.\n\n'
    provenance+='\n'.join(f'- [{s["path"]}]({Path(s["path"]).as_uri()}) — {s["recorded"]} records.' for s in sources)
    provenance+=f'\n\nDuplicate trial records skipped: {duplicates}.\n'+ '\n'.join('- '+w for w in warnings)+'\n'
    provenance+='\n## Detailed comparison charts\n\nSee [all ten labeled figures](charts.md) and [exact chart counts](chart_counts.csv). Outcome counts include all requests; valid-command accuracy and invalid-request handling are plotted separately.\n'
    report.write_text(report.read_text(encoding='utf-8').replace('See captures/index.jsonl for numbered trial folders and per-trial manifests.','See each original run’s captures/index.jsonl for images and manifests.')+provenance,encoding='utf-8')
