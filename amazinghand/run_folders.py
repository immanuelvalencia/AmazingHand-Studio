"""Short, readable Windows-safe run directories."""
import re
from datetime import datetime
from pathlib import Path


def study_metadata(dataset, metadata):
    result=dict(metadata)
    versions=sorted({str(row['suite_version']) for row in dataset if row.get('suite_version')})
    name=result.get('command_set') or result.get('study_name') or ('+'.join(versions) if versions else f'custom-{len(dataset)}')
    label=re.sub(r'\bstudy\b|\bonly\b','',name.lower())
    label=re.sub(r'[^a-z0-9._-]+','-',label).strip(' .-')[:40] or 'custom'
    result.update(study_name=name,study_label=label,study_versions=versions,selected_case_count=len(dataset))
    result.setdefault('command_set',name)
    return result


def create_run_folder(parent, models=(), deterministic=False, now=None, study=None, session=None):
    labels=['det'] if deterministic else []
    for model in dict.fromkeys(models):
        name=str(model).removesuffix(':latest')
        name=re.sub(r'[^A-Za-z0-9._-]+','-',name).strip(' .-') or 'model'
        labels.append(name[:32])
    # Full model identifiers remain in the manifest.
    label='-'.join(labels) or 'run'
    if len(label)>110:
        label=label[:95].rstrip('.-')+'-more'
    study_label=re.sub(r'[^A-Za-z0-9._-]+','-',str(study)).strip(' .-')[:40] if study else ''
    stem=(now or datetime.now()).strftime('%Y%m%d_%H%M%S')+'_'+(study_label+'_' if study_label else '')+label
    if session:
        stem+='_'+re.sub(r'[^A-Za-z0-9_-]+','-',str(session))[:40]
    parent=Path(parent);parent.mkdir(parents=True,exist_ok=True)
    index=1
    while True:
        target=parent/(stem if index==1 else f'{stem}_{index:02d}')
        try:
            target.mkdir()
            return target
        except FileExistsError:
            index+=1
