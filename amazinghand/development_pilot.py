"""Interpretation-only development check. Never dispatches to a hand or edits results."""
import json
import threading
from collections import Counter
from .experiments import ROOT, RunStore, TracedClient, trial
from .command_inference import INFERENCE_VERSION


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='mistral-nemo:latest')
    parser.add_argument('--dataset',help='Optional independently specified command JSON')
    args=parser.parse_args()
    from pathlib import Path
    from .experiments import validate_dataset
    source=Path(args.dataset) if args.dataset else ROOT/'configs/experiments/four_finger_200/commands.json'
    dataset=validate_dataset(json.loads(source.read_text(encoding='utf-8')))
    settings={'ollama_url':'http://127.0.0.1:11434','timeout_seconds':120}
    store=RunStore(dataset,settings,{'purpose':'development pilot on previously inspected study',
        'execution_backend':'interpretation_only','repetitions':1,'models':[args.model],
        'no_hardware':True,'no_capture_or_pose_evaluation':True,'dataset_source':str(source)},parent=ROOT/'experiments/data/development_pilots')
    print('PILOT',store.path,flush=True)
    cancel=threading.Event()
    with TracedClient(settings['ollama_url'],settings['timeout_seconds']) as client:
        store.write('ollama_tags.json',client.request('/api/tags'))
        client.warm(args.model)
        client.chat([{'role':'user','content':'Open the hand.'}],args.model)
        for i,case in enumerate(dataset,1):
            row,_=trial(case,'hybrid',args.model,client,cancel)
            row.update(repetition=1,order=i,mode='development_interpretation_only')
            store.append(row)
            if i%10==0:print(f'{i}/{len(dataset)} complete',flush=True)
    main_rows=[r for r in store.rows if r['category']!='robustness']
    robustness=[r for r in store.rows if r['category']=='robustness']
    report={'inference_version':INFERENCE_VERSION,'model':args.model,'scope':'Development pilot; one repetition, no hardware. Not independent publication evidence.',
        'valid_correct':sum(r['outcome']=='correct' for r in main_rows),'valid_n':len(main_rows),
        'valid_accuracy_pct':100*sum(r['outcome']=='correct' for r in main_rows)/len(main_rows),
        'robust_no_plan':sum(not r['plan'] and r['outcome'] not in ('error','cancelled') for r in robustness),
        'robust_n':len(robustness),'errors':sum(r['outcome']=='error' for r in store.rows),
        'model_calls':sum(r['llm_calls'] for r in store.rows),
        'outcomes':dict(Counter(r['outcome'] for r in store.rows))}
    store.export();store.write('pilot_summary.json',report)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
