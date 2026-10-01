"""Charts derived only from recorded interpretation trials."""
import json
from pathlib import Path
from collections import defaultdict, Counter


def export_evaluations(store):
    import csv
    fields=['trial_id','case_id','category','algorithm','model','evaluation_status','pose_sequence_match',
            'no_motion_behavior_match','max_motor_error_deg','max_modeled_tip_error_mm']
    stages=defaultdict(Counter)
    with (store.path/'evaluation.csv').open('w',newline='',encoding='utf-8-sig') as out:
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
        for row in store.rows:
            e=row.get('evaluation',{});errors=e.get('step_errors',[])+e.get('dynamic_pose_errors',[])
            writer.writerow({**{k:row.get(k) for k in fields[:5]},'evaluation_status':e.get('status'),
                'pose_sequence_match':e.get('pose_sequence_match'),'no_motion_behavior_match':e.get('behavior_match'),
                'max_motor_error_deg':max((v['max_motor_target_error_deg'] for v in errors),default=None),
                'max_modeled_tip_error_mm':max((max(v['modeled_fingertip_errors_mm'].values()) for v in errors),default=None)})
            stages[('robustness' if row['category']=='robustness' else 'main',row['algorithm'],row['model'])][row['outcome']]+=1
    store.write('stage_outcomes.json',[{'stage':s,'algorithm':a,'model':m,'outcomes':dict(c)} for (s,a,m),c in stages.items()])


def write_report(directory):
    """Rebuild a readable audit from saved records, without modifying trial data."""
    import statistics
    directory = Path(directory)
    def read(name, default):
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    rows = [json.loads(line) for line in (directory / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r.get("algorithm") in ("deterministic", "llm", "hybrid")]
    manifest = read("manifest.json", {})
    meta = manifest.get("metadata", {})
    completion = read("completion.json", {})
    study = read("study_completion.json", {})
    groups = defaultdict(list)
    for row in rows: groups[(row["algorithm"], row.get("model", ""))].append(row)
    def label(key): return {"deterministic":"Rules-only", "llm":"LLM-only", "hybrid":"Hybrid"}[key[0]]
    def safe(value): return str(value).replace("|", "\\|").replace("\n", " ")
    def score(rr): return sum(r.get("outcome") == "correct" for r in rr)
    def pct(n, d): return f"{100*n/d:.1f}%" if d else "N/A"
    lines = ["# Experiment report", "",
             f"Run: `{directory.name}`. Backend: {meta.get('execution_backend', 'not recorded')}. Repetitions: {meta.get('repetitions', 'not recorded')}.", "",
             f"Command set: {meta.get('command_set','not recorded')}. Selected cases: {meta.get('selected_case_count','not recorded')}.", "",
             f"Recorded **{len(rows)}/{completion.get('scheduled', 'not recorded')}** scheduled trials. "
             f"Status: **{'stopped / partial' if study.get('cancelled') else 'incomplete' if study.get('error') else 'complete' if completion.get('complete') else 'incomplete or not recorded'}**. "
             f"Interpretation errors: **{sum(r.get('outcome') == 'error' for r in rows)}**; "
             f"evaluator errors: **{sum(r.get('evaluation', {}).get('status') == 'error' for r in rows)}**.", "",
             "## Main command comparison", "",
             "Accuracy uses all recorded main-command attempts, including errors and rejections. Robustness cases are reported separately. Unrun trials are not counted as successes or failures.", "",
             "| Method | Model | Correct / attempted | Accuracy | Median latency (s) |", "|---|---|---:|---:|---:|"]
    for key, rr in groups.items():
        main = [r for r in rr if r.get("category") != "robustness"]
        times = [r["interpretation_seconds"] for r in main if isinstance(r.get("interpretation_seconds"), (int,float))]
        latency = f"{statistics.median(times):.6f}" if times else "N/A"
        lines.append(f"| {label(key)} | {safe(key[1] or 'Shared baseline')} | {score(main)}/{len(main)} | {pct(score(main),len(main))} | {latency} |")
    lines += ["", "Latency includes timed error attempts and excludes warm-up, reference checking and action playback.", "", "## Findings", ""]
    for (algorithm, model), rr in groups.items():
        if algorithm != "hybrid" or ("llm",model) not in groups: continue
        baseline = groups[("llm",model)]
        main = [r for r in rr if r.get("category") != "robustness"]
        other = [r for r in baseline if r.get("category") != "robustness"]
        paired = {(r['case_id'],r.get('repetition',1)) for r in main} == {(r['case_id'],r.get('repetition',1)) for r in other}
        if main and other and paired:
            delta = 100*(score(main)/len(main)-score(other)/len(other))
            lines.append(f"- **{safe(model)}:** hybrid accuracy differs from LLM-only by **{delta:+.1f} percentage points** on matched main-command attempts.")
        calls = sum(r.get('llm_calls',0) for r in rr); basecalls = sum(r.get('llm_calls',0) for r in baseline)
        matched = {(r['case_id'],r.get('repetition',1)) for r in rr} == {(r['case_id'],r.get('repetition',1)) for r in baseline}
        if matched and basecalls:
            lines.append(f"- **{safe(model)}:** hybrid used **{calls}** model calls versus **{basecalls}**, a **{100*(1-calls/basecalls):.1f}%** reduction across all matched cases.")
    lines += ["", "## Category results", "", "| Method | Model | Category | Correct / attempted |", "|---|---|---|---:|"]
    for key, rr in groups.items():
        for category in sorted({r['category'] for r in rr} - {'robustness'}):
            sub = [r for r in rr if r['category'] == category]
            lines.append(f"| {label(key)} | {safe(key[1] or 'none')} | {category} | {score(sub)}/{len(sub)} |")
    lines += ["", "## Pose and no-motion checks", "", "| Method | Model | Pose/sequence matches | No-motion checks passed |", "|---|---|---:|---:|"]
    for key, rr in groups.items():
        ev = [r.get('evaluation',{}) for r in rr]
        poses = [e for e in ev if 'pose_sequence_match' in e]; noop = [e for e in ev if 'behavior_match' in e]
        lines.append(f"| {label(key)} | {safe(key[1] or 'none')} | {sum(e['pose_sequence_match'] for e in poses)}/{len(poses)} | {sum(e['behavior_match'] for e in noop)}/{len(noop)} |")
    refs = read('reference_execution_checks.json', []); saves = read('persistence_checks.json', [])
    captures = [r['captures'] for r in rows if 'captures' in r]
    if captures:
        lines += ["", f"Model captures: **{sum(c.get('image_count',c.get('frame_count',0)) for c in captures)} images** across **{len(captures)} trials**; capture errors: **{sum(c.get('status') == 'error' for c in captures)}**. See captures/index.jsonl for numbered trial folders and per-trial manifests."]
    lines += ["", f"Controller-reference checks: **{sum(r['passed'] for r in refs)}/{len(refs)}**. Save/reload checks: **{sum(r['fields_equal'] for r in saves)}/{len(saves)}**.", "",
              "Pose agreement compares commanded configurations with frozen software references. It is not measured physical pose accuracy. A no-motion rejection can pass the behavioral criterion even when it is not an exact action-plan match.", "",
              "## Commands needing attention", "", "Repeated failures are grouped by command, method, model and outcome. Expected no-motion rejections that passed their behavioral check are excluded.", "",
              "| Case | Method | Model | Command | Outcome | Count | Recorded error |", "|---|---|---|---|---|---:|---|"]
    failures = Counter()
    for r in rows:
        if r.get('outcome') != 'correct' and not r.get('evaluation',{}).get('behavior_match',False):
            failures[(r['case_id'],r['algorithm'],r.get('model',''),r['command'],r.get('outcome'),r.get('error',''))] += 1
    for (case, algorithm, model, command, outcome, error), count in failures.items():
        lines.append(f"| {case} | {label((algorithm,model))} | {safe(model or 'none')} | {safe(command)} | {outcome} | {count} | {safe(error) or '—'} |")
    if not failures: lines += ["", "No recorded command failures under these criteria."]
    lines += ["", "## Interpretation", "", "These are descriptive results for the recorded commands. Repetitions and paired paraphrases are not independent new tasks. Preserve the frozen command set, model settings and source snapshot when comparing runs.", "",
              "![Interpretation outcomes](fig_experiment_outcomes.png)", "", "![Interpretation latency](fig_experiment_latency.png)", "",
              "Chart latency uses non-error, non-cancelled records across all categories; the main-command table above includes timed errors. Raw plans, model outputs and per-step checks are retained in trials.jsonl."]
    path = directory / 'report.md'
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    from .findings_report import export_findings
    export_findings(rows,directory)
    if (directory/'aggregation.json').exists():
        from .experiment_aggregate import append_provenance
        append_provenance(directory)
    return path


def make_charts(directory):
    if (Path(directory)/'trials.jsonl').stat().st_size:
        from .aggregate_charts import make_aggregate_charts
        return make_aggregate_charts(directory)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    directory = Path(directory)
    rows = [json.loads(line) for line in (directory / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    groups = defaultdict(list)
    for row in rows:
        if row["algorithm"] != "controller_reference":
            groups[(row["algorithm"], row["model"])].append(row)
    if not groups:
        for name in ('outcomes','latency'):
            fig,ax=plt.subplots(figsize=(6,3.6),layout='constrained')
            ax.axis('off');ax.text(.5,.5,'No recorded interpretation trials',ha='center',va='center')
            for ext in ('svg','png'):fig.savefig(directory/f'fig_experiment_{name}.{ext}',dpi=300)
            plt.close(fig)
        return
    labels = [a + ("\n" + m if m else "") for a,m in groups]
    fig, ax = plt.subplots(figsize=(max(6, len(labels)*1.5), 3.6), layout="constrained")
    base = [0]*len(labels)
    for outcome, color in [("correct", "#237c6b"), ("incorrect", "#c75146"), ("unresolved", "#dfb24c"),
                           ("rejected", "#8873a5"), ("error", "#777777"), ("cancelled", "#bbbbbb")]:
        counts = [Counter(r["outcome"] for r in values)[outcome] for values in groups.values()]
        ax.bar(labels, counts, bottom=base, label=outcome, color=color)
        base = [a+b for a,b in zip(base, counts)]
    ax.set_ylabel("Recorded trials"); ax.legend(fontsize=8, ncol=3); ax.spines[["top", "right"]].set_visible(False)
    for ext in ("svg", "png"): fig.savefig(directory / f"fig_experiment_outcomes.{ext}", dpi=300)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(max(6, len(labels)*1.5), 3.6), layout="constrained")
    values = [[r["interpretation_seconds"] for r in group if r["outcome"] not in ("error", "cancelled")] for group in groups.values()]
    valid = [(label, data) for label, data in zip(labels, values) if data]
    if valid:
        ax.boxplot([v for _,v in valid], tick_labels=[l for l,_ in valid], showfliers=True)
    ax.set_ylabel("Interpretation latency (s)"); ax.spines[["top", "right"]].set_visible(False)
    for ext in ("svg", "png"): fig.savefig(directory / f"fig_experiment_latency.{ext}", dpi=300)
    plt.close(fig)
