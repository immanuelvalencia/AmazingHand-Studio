"""Explicit denominators and annotated aggregate comparison figures."""
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

OUTCOMES=('correct','incorrect','unresolved','rejected','error','cancelled')
METHOD_COLORS={'deterministic':'#2563eb','llm':'#d97706','hybrid':'#0f766e'}
DIRECTIONS={'correct':'Higher rate is better; compare counts only at equal sample sizes',
 'incorrect':'Lower rate is better; compare counts only at equal sample sizes',
 'unresolved':'Lower on valid requests; unresolved invalid requests may be appropriate',
 'rejected':'Context dependent: desired for invalid requests, undesired for valid requests',
 'error':'Lower is better', 'cancelled':'Completion indicator; not an accuracy ranking'}
CHARTS={'Summary counts':'outcomes', **{o.capitalize():o for o in OUTCOMES},
        'Valid-command accuracy':'accuracy','Invalid-request handling':'robustness','Latency':'latency'}


def make_aggregate_charts(directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    directory=Path(directory)
    rows=[json.loads(line) for line in (directory/'trials.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    groups=defaultdict(list)
    for row in rows:
        if row.get('algorithm') in ('deterministic','llm','hybrid'):
            groups[(row['algorithm'],row.get('model',''))].append(row)
    keys=sorted(groups,key=lambda k:(bool(k[1]),k[1],{'deterministic':0,'llm':1,'hybrid':2}[k[0]]))
    if not keys:return
    names={'deterministic':'Deterministic','llm':'LLM-only','hybrid':'Hybrid'}
    labels=[(k[1].removesuffix(':latest')+' · ' if k[1] else '')+names[k[0]] for k in keys]
    counts=[Counter(r['outcome'] for r in groups[k]) for k in keys]
    extras=sorted(set().union(*(set(c) for c in counts))-set(OUTCOMES))
    outcomes=list(OUTCOMES)+extras
    totals=[len(groups[k]) for k in keys]
    summary=[]
    for key,label,count in zip(keys,labels,counts):
        rr=groups[key];main=[r for r in rr if r['category']!='robustness'];robust=[r for r in rr if r['category']=='robustness']
        times=[r['interpretation_seconds'] for r in main if isinstance(r.get('interpretation_seconds'),(int,float))]
        summary.append({'method':names[key[0]],'model':key[1],'attempts':len(rr),**{o:count[o] for o in outcomes},
            'valid_attempts':len(main),'valid_correct':sum(r['outcome']=='correct' for r in main),
            'invalid_attempts':len(robust),'invalid_no_plan':sum(not r.get('plan') and r['outcome'] not in ('error','cancelled') for r in robust),
            'median_valid_latency_s':statistics.median(times) if times else None,
            'p95_valid_latency_s':float(np.percentile(times,95)) if times else None})
    with (directory/'chart_counts.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    height=max(3.8,1.7+.48*len(keys))
    colors=[METHOD_COLORS[k[0]] for k in keys]
    def save(fig,name):
        from matplotlib.patches import Patch
        fig.legend(handles=[Patch(color=METHOD_COLORS[k],label=names[k]) for k in METHOD_COLORS],
                   loc='outside lower center',ncol=3,frameon=False)
        for axis in fig.axes:
            for tick,color in zip(axis.get_yticklabels(),colors):tick.set_color(color)
        for ext in ('png','svg'):fig.savefig(directory/f'fig_experiment_{name}.{ext}',dpi=220,bbox_inches='tight')
        plt.close(fig)
    def bars(title,xlabel):
        fig,ax=plt.subplots(figsize=(11,height),layout='constrained')
        ax.set_yticks(range(len(keys)),labels);ax.invert_yaxis();ax.set_title(title,loc='left',fontweight='bold',pad=15)
        ax.set_xlabel(xlabel);ax.spines[['top','right']].set_visible(False);ax.xaxis.grid(True,alpha=.15);ax.set_axisbelow(True)
        return fig,ax
    # One independent count figure per outcome, including zero counts.
    for outcome in OUTCOMES:
        fig,ax=bars(outcome.capitalize()+' outcomes · all recorded attempts\n'+DIRECTIONS[outcome],'Number of trials (valid and invalid requests combined)')
        values=[c[outcome] for c in counts];ax.barh(range(len(keys)),values,color=colors,height=.6)
        ax.set_xlim(0,max(1,max(values))*1.35)
        from matplotlib.ticker import MaxNLocator
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        for y,(value,total) in enumerate(zip(values,totals)):
            ax.annotate(f'{value:,} / {total:,} ({100*value/total:.1f}%)',(value,y),xytext=(6,0),textcoords='offset points',va='center',fontsize=9)
        save(fig,outcome)
    # A count matrix is easier to compare than many stacked, unlabeled segments.
    matrix=np.array([[c[o] for o in outcomes]+[n] for c,n in zip(counts,totals)])
    fig,ax=plt.subplots(figsize=(12,height),layout='constrained')
    ax.imshow(matrix,cmap='Greys',aspect='auto',vmin=0,vmax=max(1,int(matrix.max())*1.7))
    ax.set_xticks(range(len(outcomes)+1),[o.capitalize() for o in outcomes]+['Total'],rotation=25,ha='right')
    ax.set_yticks(range(len(keys)),labels);ax.set_title('Outcome summary · exact counts across all recorded attempts\nCorrect: higher; incorrect/errors: lower; rejection/unresolved: context dependent',loc='left',fontweight='bold',pad=16)
    for y in range(len(keys)):
        for x in range(len(outcomes)+1):ax.text(x,y,f'{matrix[y,x]:,}',ha='center',va='center',fontsize=10)
    save(fig,'outcomes')
    for name,title,numerator,denominator in [('accuracy','Valid commands · exact-plan accuracy','valid_correct','valid_attempts'),
        ('robustness','Invalid requests · no plan produced without runtime error/cancellation','invalid_no_plan','invalid_attempts')]:
        fig,ax=bars(title+'\nHigher is better','Percent of recorded attempts in this category')
        values=[100*r[numerator]/r[denominator] if r[denominator] else 0 for r in summary]
        ax.barh(range(len(keys)),values,color=colors,height=.6);ax.set_xlim(0,135);ax.set_xticks([0,20,40,60,80,100])
        for y,(value,r) in enumerate(zip(values,summary)):
            label=f'{r[numerator]:,}/{r[denominator]:,} · {value:.1f}%' if r[denominator] else 'N/A — no attempts'
            ax.annotate(label,(value,y),xytext=(6,0),textcoords='offset points',va='center',fontsize=9)
        save(fig,name)
    fig,ax=bars('Valid-command interpretation latency · recorded timed attempts\nLower is better, considered alongside accuracy','Seconds; includes timed errors, excludes playback and warm-up')
    for y,r in enumerate(summary):
        median,p95=r['median_valid_latency_s'],r['p95_valid_latency_s']
        if median is None:ax.text(0,y,'N/A',va='center');continue
        ax.plot([median,p95],[y,y],color=colors[y],linewidth=3)
        ax.plot(median,y,'o',color=colors[y],label='Median' if y==0 else None)
        ax.plot(p95,y,'|',color=colors[y],markersize=12,label='95th percentile' if y==0 else None)
        ax.annotate(f'{median:.4f} / {p95:.4f} s',(p95,y),xytext=(7,0),textcoords='offset points',va='center',fontsize=9)
    ax.set_xlim(0,max([r['p95_valid_latency_s'] or 0 for r in summary]+[.001])*1.6)
    if ax.get_legend_handles_labels()[0]:ax.legend(loc='lower right')
    save(fig,'latency')
    guide=['# Aggregate figures','',
           'Method colors: blue = Deterministic; orange = LLM-only; teal = Hybrid. Higher valid-command accuracy and invalid-request no-plan rate are better. Lower latency and runtime-error rate are better. Model calls are a cost measure, to be considered alongside accuracy. Rejections and unresolved requests require valid/invalid context; cancelled trials indicate incomplete collection.', '',
           'Count figures include all recorded attempts, including invalid requests. Labels show count / attempts and percentage. Rejected requests are not automatically errors: rejection can be appropriate for invalid commands. Unresolved means no actionable plan for a request expected to produce one; error means a runtime/interpretation failure. Cancelled is reported separately.',
           '', 'Valid-command accuracy and invalid-request no-plan handling use separate denominators. No-plan handling is not a physical safety measurement. Independent repetitions are pooled descriptively; unequal sample sizes are visible. Full numerical values are in chart_counts.csv.','']
    for label,name in CHARTS.items():guide+= [f'## {label}','',f'![{label}](fig_experiment_{name}.png)','']
    (directory/'charts.md').write_text('\n'.join(guide),encoding='utf-8')
