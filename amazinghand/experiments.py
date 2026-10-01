"""Reproducible command comparisons. This module never moves hardware."""
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import time
import uuid

from amazinghand.chat.chat_session import ChatSession
from amazinghand.chat.commands import PARSER_VERSION
from amazinghand.chat.logic import Answer, FINGER_NAMES, motion_plan, validate_answer
from amazinghand.chat.cancellation import Cancelled
from .fast_chat import FastOllamaClient, FAST_PROMPT, FAST_SCHEMA, INFERENCE_VERSION

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_ROOT = ROOT / "experiments"
DATA_ROOT = EXPERIMENTS_ROOT / "data"
RUNS_ROOT = DATA_ROOT / "experiments"
AGGREGATES_ROOT = DATA_ROOT / "comparisons"
ALGORITHMS = ("deterministic", "llm", "hybrid")


def machine_details():
    result = {}
    commands = {"gpu": ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]}
    if os.name == "nt":
        commands["computer"] = ["powershell", "-NoProfile", "-Command",
            "$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor; "
            "@{RAM_bytes=$c.TotalPhysicalMemory; CPU=$p.Name} | ConvertTo-Json -Compress"]
    for name, command in commands.items():
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result[name] = completed.stdout.strip() if completed.returncode == 0 else {"error": completed.stderr.strip()}
        except Exception as exc: result[name] = {"error": str(exc)}
    return result


def word_error_rate(reference, transcript):
    import re
    a, b = (re.findall(r"\w+", text.casefold()) for text in (reference, transcript))
    previous = list(range(len(b)+1))
    for i, word in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(word != other)))
        previous = current
    return previous[-1]/len(a) if a else None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def expected_answer(data):
    return validate_answer({"answer": "Reference action", "repeat": data.get("repeat", 1),
                            "steps": data["steps"]})


def signature(answer):
    """Compare ordered executable actions, including speed and hold, not prose."""
    return [asdict(move) for move in motion_plan(answer)]


def validate_dataset(data):
    if not isinstance(data, list) or not data:
        raise ValueError("Dataset must be a nonempty JSON array.")
    ids = set()
    for row in data:
        if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            raise ValueError("Every command needs a unique nonempty string id.")
        ids.add(row["id"])
        if not isinstance(row.get("command"), str) or not 1 <= len(row["command"].strip()) <= 2000:
            raise ValueError(f"{row['id']}: command must contain 1..2000 characters.")
        if not isinstance(row.get("category"), str) or not row["category"]:
            raise ValueError("Every command needs a category.")
        expected_answer(row["expected"])
        for name, value in row.get("targets", {}).items():
            if name not in FINGER_NAMES or (value is not None and
                    (type(value) not in (int, float) or not 0 <= value <= 100)):
                raise ValueError(f"{row['id']}: invalid initial target.")
        if row.get("previous"):
            expected_answer(row["previous"])
    return data


def starter_dataset():
    """200-case control suite; expected targets remain evaluator-only."""
    from .experiment_cases import balanced_200
    return validate_dataset(balanced_200())


class TracedClient(FastOllamaClient):
    calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return super().chat(*args, **kwargs)


def interpret(case, algorithm, model, client, trace=None):
    """Identical context and final validation for all three routes."""
    if not isinstance(case["command"], str) or not 1 <= len(case["command"].strip()) <= 2000:
        raise ValueError("Command must contain 1..2000 characters.")
    session = ChatSession()
    session.targets.update(case.get("targets", {}))
    if case.get("previous"):
        session.last_answer = expected_answer(case["previous"])
        session.commit("Previous reference command", session.last_answer)
    if algorithm == "llm":
        context = "Current symbolic commanded finger opening percentages (null = unknown, not measured): " + json.dumps(session.targets)
        answer = client.chat([*session.history, {"role": "user", "content": case["command"].strip()}], model, context=context)
    elif algorithm in ("deterministic", "hybrid"):
        if algorithm == "deterministic":
            class NoFallback:
                def chat(self, *args, **kwargs):
                    return Answer("unknown", text="No deterministic rule matched.")
            client = NoFallback()
        try:
            answer = session.plan(client, case["command"], model)
        finally:
            if trace is not None:
                trace.update(session.last_parse_decision or {})
    else:
        raise ValueError("Unknown algorithm.")
    motion_plan(answer)
    return answer


def trial(case, algorithm, model, client, cancel):
    if cancel.is_set():
        raise Cancelled()
    client.calls = 0
    client.last_raw = ""
    client.last_metrics = None
    client.cancel = cancel
    result = {"trial_id": uuid.uuid4().hex, "utc": datetime.now(timezone.utc).isoformat(),
              "case_id": case["id"], "category": case["category"], "command": case["command"],
              "algorithm": algorithm, "model": model if algorithm != "deterministic" else "",
              "expected": case["expected"], "expected_plan": signature(expected_answer(case["expected"])),
              "targets": case.get("targets", {}), "previous": case.get("previous"),
              "answer": None, "plan": None, "error": ""}
    for key in ("suite_version", "pair_id", "wording", "skill", "tested_finger", "tested_pair"):
        if key in case:
            result[key] = case[key]
    answer = None
    start = time.perf_counter()
    parser_trace = {}
    try:
        # Interpreter input is explicitly allowlisted: evaluation references never cross this boundary.
        stimulus = {key: case[key] for key in ("command", "targets", "previous") if key in case}
        answer = interpret(stimulus, algorithm, model, client, trace=parser_trace)
        if cancel.is_set():
            raise Cancelled()
        elapsed = time.perf_counter() - start
        result["answer"] = asdict(answer)
        result["plan"] = signature(answer)
        result["outcome"] = ("correct" if result["plan"] == result["expected_plan"] else
                             "unresolved" if not result["plan"] else "incorrect")
    except Cancelled:
        elapsed = time.perf_counter() - start
        result.update(outcome="cancelled", error="Operator cancelled")
    except (ValueError, TypeError) as exc:
        elapsed = time.perf_counter() - start
        result.update(outcome="rejected", error=str(exc))
    except Exception as exc:
        elapsed = time.perf_counter() - start
        result.update(outcome="error", error=str(exc))
    result.update(parser_decision=parser_trace, parser_version=PARSER_VERSION,
                  interpretation_seconds=elapsed, llm_calls=client.calls,
                  route="llm" if client.calls else "deterministic",
                  interpretation_start_perf_counter=start, interpretation_end_perf_counter=start+elapsed,
                  raw_response=getattr(client, "last_raw", ""), metrics=client.last_metrics)
    return result, answer


class RunStore:
    def __init__(self, dataset, settings, metadata, parent=None):
        parent = Path(parent or RUNS_ROOT)
        from .run_folders import create_run_folder, study_metadata
        metadata=study_metadata(dataset,metadata)
        models=metadata.get('models') or ([metadata['model']] if metadata.get('model') else [])
        baseline=metadata.get('include_deterministic', metadata.get('mode') in ('complete_automated_study','matched_text'))
        baseline=baseline or metadata.get('algorithm')=='deterministic'
        self.path = create_run_folder(parent, models, baseline, study=metadata['study_label'],session=metadata.get('session_id'))
        self.rows = []
        source_hashes = {}
        for source in [Path(__file__), ROOT / "amazinghand/fast_chat.py", ROOT / "amazinghand/experiment_cases.py", ROOT / "amazinghand/experiment_capture.py", ROOT / "amazinghand/experiment_report.py", ROOT / "amazinghand/chat/commands.py",
                       ROOT / "amazinghand/chat/logic.py", ROOT / "amazinghand/chat/chat_session.py",
                       ROOT / "amazinghand/assistant_engine.py", ROOT / "amazinghand/chat/cancellation.py",
                       ROOT / "amazinghand/experiment_protocol.py",
                       ROOT / "amazinghand/gestures.py", ROOT / "amazinghand/kinematics.py", ROOT / "amazinghand/simulation.py",
                       ROOT / "amazinghand/automated_study.py",
                       ROOT / "amazinghand/four_finger_study.py", ROOT / "amazinghand/command_inference.py",
                       ROOT / "amazinghand/experiment_session.py", ROOT / "amazinghand/run_folders.py"]:
            content=source.read_bytes()
            source_hashes[str(source.relative_to(ROOT))] = hashlib.sha256(content).hexdigest()
            snapshot=self.path / "source_snapshot" / source.relative_to(ROOT)
            snapshot.parent.mkdir(parents=True,exist_ok=True)
            snapshot.write_bytes(content)
        self.write("manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
                   "dataset_sha256": digest(dataset), "settings": settings, "metadata": metadata,
                   "platform": platform.platform(), "processor": platform.processor(), "python": platform.python_version(),
                   "cpu_count": os.cpu_count(), "source_hashes": source_hashes, "parser_version": PARSER_VERSION,
                   "inference_version": INFERENCE_VERSION,
                   "prompt": FAST_PROMPT, "schema": FAST_SCHEMA,
                   "inference": {"temperature": 0, "num_ctx": 8192, "num_predict": 768, "stream": True, "think": False},
                   "timing": "perf_counter: interpreter entry to complete validated plan or terminal error; excludes scoring, speech and hardware",
                   "scoring": "Exact ordered motion-plan match including hold and speed; prose ignored; manually review equivalent encodings",
                   "status": "started"})
        self.write("dataset.json", dataset)

    def write(self, name, data):
        target = self.path / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)

    def append(self, row, filename="trials.jsonl"):
        with (self.path / filename).open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        if filename == "trials.jsonl":
            self.rows.append(row)

    def export(self):
        fields = ["trial_id", "case_id", "category", "command", "algorithm", "model", "repetition", "outcome",
                  "interpretation_seconds", "llm_calls", "route", "error", "mode", "physical", "controller_completed",
                  "first_position_write_completed_seconds", "voice_to_plan_seconds",
                  "voice_to_first_position_write_completed_seconds", "controller_finished_seconds", "execution_error"]
        fields += ["execution_backend", "kinematics_playback_completed", "first_preview_target_seconds", "execution_seconds"]
        fields += ["source_run", "source_repetition"]
        with (self.path / "trials.csv").open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows)
        groups = defaultdict(list)
        for row in self.rows:
            groups[(row["algorithm"], row["model"])].append(row)
        summary = []
        for (algorithm, model), rows in groups.items():
            completed = [r for r in rows if r["outcome"] in ("correct", "incorrect", "unresolved", "rejected")]
            counts = Counter(r["outcome"] for r in rows)
            summary.append({"algorithm": algorithm, "model": model, "attempted": len(rows),
                "completed": len(completed), "outcomes": dict(counts),
                "correct_fraction_completed": counts["correct"] / len(completed) if completed else None,
                "llm_calls": sum(r["llm_calls"] for r in rows),
                "median_interpretation_seconds_completed": statistics.median(r["interpretation_seconds"] for r in completed) if completed else None})
        self.write("summary.json", summary)
        breakdown = defaultdict(Counter)
        for row in self.rows:
            breakdown[(row["algorithm"], row["model"], row["category"], row["route"])][row["outcome"]] += 1
        self.write("category_route_counts.json", [{"algorithm": a, "model": m, "category": c, "route": r,
                                                   "outcomes": dict(counts)} for (a,m,c,r), counts in breakdown.items()])
        return summary


def run_matrix(dataset, models, repetitions, seed, settings, store, cancel, emit, execute=None, evaluate=None, prepare=None, include_deterministic=True):
    if not models and not include_deterministic:
        raise ValueError('Select deterministic or at least one model.')
    rng = random.Random(seed)
    schedule = []
    # One shared baseline, then contiguous model blocks to avoid model thrashing.
    # Randomize commands and LLM/hybrid order within each repetition.
    for model in ([None] if include_deterministic else []) + list(dict.fromkeys(models)):
        for rep in range(1, repetitions + 1):
            cases = list(dataset)
            rng.shuffle(cases)
            for case in cases:
                algorithms = ["deterministic"] if model is None else ["llm", "hybrid"]
                rng.shuffle(algorithms)
                schedule.extend((rep, case, a, model or "") for a in algorithms)
    store.write("execution_order.json", {"strategy": "sequential_model_blocks", "models": list(dict.fromkeys(models)),
                                         "baseline": "shared" if include_deterministic else "excluded", "seed": seed})
    store.write("schedule.json", [{"repetition": r, "case_id": c["id"], "algorithm": a, "model": m} for r,c,a,m in schedule])
    emit("progress", {"completed":0,"total":len(schedule)})
    try:
        store.write("machine.json", machine_details())
        with TracedClient(settings["ollama_url"], settings["timeout_seconds"]) as client:
            client.cancel = cancel
            if models:
                store.write("ollama_tags.json", client.request("/api/tags"))
                store.write("ollama_version.json", client.request("/api/version"))
            warmed = set()
            for i, (rep, case, algorithm, model) in enumerate(schedule):
                if cancel.is_set():
                    break
                if model:
                    emit("status", f"Preparing {model} · trial {i+1}/{len(schedule)}")
                    # Rewarm each model condition: interleaving may evict another model.
                    client.warm(model)
                    if model not in warmed:
                        if cancel.is_set(): break
                        emit("status", f"Excluded warm-up inference · {model}")
                        warm_case = {"id": "warmup", "category": "warmup", "command": "Open the hand",
                                     "expected": {"steps": [{"action": "open"}]}}
                        warm_row, _ = trial(warm_case, "llm", model, client, cancel)
                        store.append(warm_row, "warmups.jsonl")
                        if warm_row["outcome"] in ("error", "cancelled"):
                            raise RuntimeError("Warm-up failed: " + warm_row["error"])
                        warmed.add(model)
                emit("status", f"{i+1}/{len(schedule)} · {case['id']} · {algorithm} · {model}")
                setup=prepare(case) if prepare is not None else None
                row, answer = trial(case, algorithm, model, client, cancel)
                row.update(repetition=rep, mode="text_benchmark", order=i+1)
                if setup is not None: row["initial_state_setup"]=setup
                if evaluate is not None and not cancel.is_set():
                    try:
                        row["evaluation"] = evaluate(case, answer, row)
                    except Exception as exc:
                        row["evaluation"] = {"status": "error", "error": str(exc)}
                if execute is not None and answer is not None and not cancel.is_set():
                    store.append(row, "interpretations.jsonl")
                    try:
                        row.update(execute(answer))
                    except Exception as exc:
                        row["execution_error"] = str(exc) or type(exc).__name__
                store.append(row)
                emit("trial", row)
                emit("progress", {"completed":len(store.rows),"total":len(schedule)})
                if row.get("execution_error"):
                    break
    finally:
        import sys
        error = str(sys.exc_info()[1]) if sys.exc_info()[1] else ""
        summary = store.export()
        store.write("completion.json", {"cancelled": cancel.is_set(), "recorded": len(store.rows), "scheduled": len(schedule),
                                      "complete": len(store.rows) == len(schedule) and not cancel.is_set(), "error": error})
        emit("summary", summary)
