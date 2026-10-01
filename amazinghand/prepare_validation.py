"""Prepare evaluator-only targets without calling an interpreter or moving hardware."""
import argparse
import json
from pathlib import Path

from .automated_study import freeze_validation, robustness_cases
from .experiments import ROOT


def prepare(commands, protocol, output):
    from .experiment_cases import complete_cases
    cases=complete_cases(commands)
    bundle=freeze_validation(cases,protocol)
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    (output/"validation_references.json").write_text(json.dumps(bundle,indent=2),encoding="utf-8")
    (output/"stimuli.json").write_text(json.dumps([{k:c[k] for k in ("id","category","command","targets","previous") if k in c} for c in cases],indent=2),encoding="utf-8")
    lines=["# Validation coverage","", "References are evaluator-only. Stimuli are stored separately; reference identifiers and target poses never enter interpreter input.","",
           "| Test | Cases | Reference |","|---|---:|---|",
           f"| Main commands | {sum(c['category'] != 'robustness' for c in cases)} | Every ordered target pose |",
           f"| Robustness | {sum(c['category']=='robustness' for c in cases)} | No-motion criterion |",
           f"| Controller poses/sequences | {len(bundle['controller_references'])} | Every expected step, including No phases |",
           "| Persistence | 10 | Exact saved configuration fields |","", "## Command coverage","",
           "| ID | Category | Criterion | Pose steps | Dynamic phases |","|---|---|---|---:|---:|"]
    for key,row in bundle["cases"].items():
        count=sum(len(v) for v in row["dynamic_frames"].values())
        lines.append(f"| {key} | {row['category']} | {row['criterion']} | {len(row['frames'])} | {count} |")
    lines += ["", "Reference checksum: "+bundle["reference_sha256"],"",
              "Targets are frozen software specifications, not measured physical poses. Ambiguous/invalid cases use no-motion expectations rather than an invented pose."]
    (output/"COVERAGE.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return bundle


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commands",type=Path,default=ROOT/"configs/experiments/commands_200.json")
    parser.add_argument("--protocol",type=Path,default=ROOT/"configs/experiments/study_protocol.json")
    parser.add_argument("--output",type=Path,default=ROOT/"configs/experiments/validation")
    args=parser.parse_args()
    bundle=prepare(json.loads(args.commands.read_text(encoding="utf-8")),json.loads(args.protocol.read_text(encoding="utf-8")),args.output)
    print(json.dumps(bundle["coverage"],indent=2))
