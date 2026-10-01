"""Read-only on-screen summaries of recorded experiments."""
import json
import statistics
from collections import defaultdict, Counter


def metrics_text(rows, directory):
    def read(name, default):
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    completion = read("completion.json", {})
    meta = read("manifest.json", {}).get("metadata", {})
    status = "Complete" if completion.get("complete") else "Partial / completion not confirmed"
    if completion.get("cancelled"): status = "Stopped"
    lines = [f"{status} · {len(rows)} recorded / {completion.get('scheduled', '—')} scheduled",
             f"Backend: {meta.get('execution_backend','not recorded')}   Repetitions: {meta.get('repetitions','—')}",
             f"Run: {directory.name}", "",
             "MAIN COMMANDS",
             "Accuracy includes recorded errors and rejections; robustness is separate.", ""]
    aggregate=read('aggregation.json',{})
    if aggregate:
        lines[0:0]=[f"AGGREGATE · {len(aggregate['sources'])} source runs · {aggregate['duplicates_skipped']} copied records skipped",
                    "Source sessions are pooled; see report for provenance and unequal sample sizes.",
                    *aggregate.get('warnings',[]), ""]
    groups = defaultdict(list)
    for row in rows:
        if row.get("algorithm") in ("deterministic", "llm", "hybrid"):
            groups[(row["algorithm"], row.get("model", ""))].append(row)
    for (algorithm, model), group in groups.items():
        name = {"deterministic":"Rules-only", "llm":"LLM-only", "hybrid":"Hybrid"}[algorithm]
        main = [r for r in group if r.get("category") != "robustness"]
        correct = sum(r.get("outcome") == "correct" for r in main)
        times = [r["interpretation_seconds"] for r in main if isinstance(r.get("interpretation_seconds"),(int,float))]
        accuracy = f"{100*correct/len(main):.1f}%" if main else "N/A"
        latency = f"{statistics.median(times):.6f} s" if times else "N/A"
        evaluations = [r.get("evaluation", {}) for r in group]
        poses = [e for e in evaluations if "pose_sequence_match" in e]
        noops = [e for e in evaluations if "behavior_match" in e]
        lines += [f"{name} · {model or 'shared baseline'}",
                  f"  Accuracy: {accuracy} ({correct}/{len(main)})   Median latency: {latency}",
                  f"  Model calls (all cases): {sum(r.get('llm_calls',0) for r in group)}",
                  f"  Outcomes (all cases): " + ", ".join(f"{k}: {v}" for k,v in Counter(r.get('outcome') for r in group).items()),
                  f"  Pose/sequence matches: {sum(e['pose_sequence_match'] for e in poses)}/{len(poses)} evaluable",
                  f"  No-motion checks passed: {sum(e['behavior_match'] for e in noops)}/{len(noops)} evaluated"]
        for category in sorted({r.get('category') for r in main}):
            sub = [r for r in main if r['category'] == category]
            lines.append(f"    {category}: {sum(r.get('outcome') == 'correct' for r in sub)}/{len(sub)} correct")
        lines.append("")
    if not groups: lines += ["No interpretation trials in this run.", ""]
    refs = read("reference_execution_checks.json", [])
    saves = read("persistence_checks.json", [])
    captures = [r['captures'] for r in rows if 'captures' in r]
    lines += ["VALIDATION",
              f"Model captures: {sum(c.get('image_count',c.get('frame_count',0)) for c in captures)} images across {len(captures)} trials; {sum(c.get('status') == 'error' for c in captures)} capture errors",
              f"Controller reference checks: {sum(r['passed'] for r in refs)}/{len(refs)}" if refs else "Controller reference checks: not recorded",
              f"Save/reload checks: {sum(r['fields_equal'] for r in saves)}/{len(saves)}" if saves else "Save/reload checks: not recorded",
              f"Evaluator errors: {sum(r.get('evaluation',{}).get('status') == 'error' for r in rows)}", "",
              "Latency includes timed errors; excludes warm-up and pose evaluation.",
              "Pose agreement is against software references, not measured physical accuracy.",
              "Use Trial records to inspect failures or Export report + charts for the full report."]
    return "\n".join(lines)
