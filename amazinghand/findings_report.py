"""Measured findings for the on-screen report and its Markdown export."""
from collections import defaultdict, Counter
import json
import statistics


def findings(rows,directory):
    groups=defaultdict(list)
    for r in rows:
        if r.get('algorithm') in ('deterministic','llm','hybrid'):groups[(r['algorithm'],r.get('model',''))].append(r)
    sections=[]
    def label(key):return (key[1]+' · ' if key[1] else '')+{'deterministic':'Deterministic','llm':'LLM-only','hybrid':'Hybrid'}[key[0]]
    total=sum(map(len,groups.values()));cases=len({r['case_id'] for rr in groups.values() for r in rr})
    sections.append(('Study overview',f'{total:,} recorded attempts · {cases:,} distinct command IDs · {len(groups)} model/method conditions.\nRun: {directory.name}'))
    sections.append(('How to read the results',
        'Method colors: blue = Deterministic; orange = LLM-only; teal = Hybrid.\n\n'
        'Higher is better: valid-command accuracy and invalid-request no-plan rate.\n'
        'Lower is better: incorrect responses on valid commands, runtime errors, and latency. Fewer model calls reduce inference use, but must be considered alongside accuracy.\n'
        'Context dependent: rejected or unresolved requests can be appropriate for invalid commands but are failures for valid commands. Cancelled trials describe incomplete collection. Compare outcome rates when sample sizes differ.'))
    ranks=[]
    for key,rr in groups.items():
        main=[r for r in rr if r['category']!='robustness']
        if main:ranks.append((sum(r['outcome']=='correct' for r in main)/len(main),key,main))
    if ranks:
        ranks.sort(key=lambda item:item[0],reverse=True)
        best=ranks[0][0];leaders=[label(k) for rate,k,rr in ranks if rate==best]
        lead=f'Highest observed valid-command accuracy: {", ".join(leaders)} ({100*best:.1f}%).' if len(ranks)>1 else 'One condition is available; no between-method ranking is possible.'
        lines=[lead]
        for rate,key,rr in ranks:
            lines.append(f'{label(key)}: {sum(r["outcome"]=="correct" for r in rr):,}/{len(rr):,} correct ({100*rate:.1f}%).')
        lines.append('Accuracy includes errors and rejections on valid requests. Differences are descriptive; repetitions are not independent new commands.')
        sections.append(('Command accuracy','\n\n'.join(lines)))
    lines=[]
    for key,rr in groups.items():
        c=Counter(r['outcome'] for r in rr)
        lines.append(label(key)+'\n'+', '.join(f'{name}: {c[name]:,}' for name in ('correct','incorrect','unresolved','rejected','error','cancelled'))+f'. Total: {len(rr):,}.')
    sections.append(('Outcome breakdown','\n\n'.join(lines)+'\n\nThese counts include valid and invalid requests. A rejection can be appropriate for an invalid request; it is not automatically a runtime error.'))
    lines=[]
    for key,rr in groups.items():
        invalid=[r for r in rr if r['category']=='robustness']
        if not invalid:continue
        passed=sum(not r.get('plan') and r['outcome'] not in ('error','cancelled') for r in invalid)
        lines.append(f'{label(key)}: {passed}/{len(invalid)} invalid requests produced no plan without runtime error/cancellation ({100*passed/len(invalid):.1f}%).')
    sections.append(('Invalid-request handling','\n\n'.join(lines) if lines else 'No invalid-request trials were recorded.'))
    lines=[]
    for key,rr in groups.items():
        times=[r['interpretation_seconds'] for r in rr if r['category']!='robustness' and isinstance(r.get('interpretation_seconds'),(float,int))]
        latency=f'{statistics.median(times):.4f} s median over {len(times)} timed valid-request attempts' if times else 'no valid-request timing available'
        calls=sum(r.get('llm_calls',0) for r in rr)
        lines.append(f'{label(key)}: {latency}; {calls:,} model calls across {len(rr):,} total attempts.')
    sections.append(('Latency and model use','\n\n'.join(lines)+'\n\nTiming includes timed errors and excludes warm-up and action playback. Cross-session latency can differ with machine load and execution order.'))
    failures=Counter((label(k),r['case_id'],r['command']) for k,rr in groups.items() for r in rr if r['category']!='robustness' and r['outcome']!='correct')
    lines=[f'{method} · {case}: {n} unsuccessful attempts — “{command}”' for (method,case,command),n in failures.most_common(5)]
    sections.append(('Commands to inspect','\n\n'.join(lines) if lines else 'No unsuccessful valid-command attempts were recorded.'))
    note='Pose agreement uses modeled reference configurations; it does not establish measured physical fingertip accuracy. Counts describe recorded attempts only.'
    completion=directory/'completion.json'
    if not completion.exists() or not json.loads(completion.read_text(encoding='utf-8')).get('complete'):
        note+=' This run is partial or its completion is unconfirmed; unrun trials are excluded.'
    sections.append(('Interpretation limits',note))
    return sections


def export_findings(rows,directory):
    sections=findings(rows,directory)
    text='# Findings report\n\n'+'\n\n'.join('## '+title+'\n\n'+body for title,body in sections)
    text+='\n\n## Figures\n\n'
    for name,title in [('accuracy','Valid-command accuracy'),('outcomes','Exact outcome counts'),('robustness','Invalid-request handling'),('latency','Interpretation latency')]:
        if (directory/f'fig_experiment_{name}.png').exists():text+=f'![{title}](fig_experiment_{name}.png)\n\n'
    (directory/'findings.md').write_text(text,encoding='utf-8')
    return sections
