"""Evaluator-only references and an unattended, model-based study workflow."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import threading
import csv
from collections import defaultdict, Counter

import numpy as np

from .assistant_engine import ChatHand
from .config import FINGER_ORDER, HandConfig
from .experiments import ROOT, digest, expected_answer, run_matrix, signature, validate_dataset
from .kinematics import AmazingHandKinematics
from .experiment_protocol import play_reference
from amazinghand.chat.chat_session import ChatSession
from amazinghand.chat.logic import motion_plan


from .experiment_cases import robustness_cases, complete_cases


def reference_library(config, protocol):
    """Frozen specifications, never produced by the interpreter under test."""
    poses = {p["id"]: deepcopy(p["offsets"]) for p in protocol["items"] if p["kind"] == "pose"}
    result = {"open": poses["G1"], "close": poses["G2"], "point": poses["G3"], "yes": poses["G6"]}
    result["peace"] = dict(deepcopy(poses["G2"]), index=[-65,15] if config.side=="left" else [-15,65],
                           middle=[-15,65] if config.side=="left" else [-65,15])
    result["ok"] = {"index":[50,-50], "middle":[0,0], "ring":[-20,20],
                    "thumb":[-12,-65] if config.side=="left" else [65,12]}
    return {"named_poses": result, "saved_study_poses": poses,
            "provenance": "G references from frozen study protocol; peace/OK specified from existing gesture definitions; percentage targets specified analytically. Shared model: software consistency, not independent physical accuracy."}


class FastCancel:
    """Skip controller hold waits during target evaluation, retain cancellation."""
    def __init__(self, parent): self.parent = parent
    def is_set(self): return self.parent.is_set()
    def wait(self, seconds): return self.parent.is_set()


class PoseEvaluator:
    def __init__(self, config, references, cancel, motor_tolerance=1.0, fingertip_tolerance_mm=2.0):
        self.config = deepcopy(config)
        self.references = deepcopy(references)
        self.cancel = cancel
        self.motor_tolerance = motor_tolerance
        self.tip_tolerance = fingertip_tolerance_mm
        self.model = AmazingHandKinematics(self.config)
        self.frozen_cases = None
        self.frozen_controller = None

    def initial(self, case):
        # Explicit common reset state for untouched fingers; commanded context remains as supplied.
        pose = {n:[0.,0.] for n in FINGER_ORDER}
        for n, value in case.get("targets", {}).items():
            if value is not None: pose[n] = [90-1.25*value, -(90-1.25*value)]
        return pose

    def expected_frames(self, case):
        pose = self.initial(case)
        frames = []
        for step in signature(expected_answer(case["expected"])):
            action, value = step["action"], step["value"]
            if action == "pose":
                for n, percent in value.items(): pose[n] = [90-1.25*percent, -(90-1.25*percent)]
            elif action in self.references["named_poses"]:
                pose = deepcopy(self.references["named_poses"][action])
            elif action in ("separator",): pose = deepcopy(self.references["named_poses"]["close"])
            elif action == "count":
                pose = {n: [-35,35] if i<value else [90,-90] for i,n in enumerate(FINGER_ORDER)}
            elif action == "no":
                pose = deepcopy(self.references["named_poses"]["close"]); pose["index"] = [-35,35]
            elif action != "relax": raise ValueError("No evaluator specification for " + action)
            frames.append(deepcopy(pose))
        return frames

    def capture(self, answer, initial):
        pose, frames, writes = deepcopy(initial), [], []
        active = [False]
        def emit(kind, value):
            if getattr(self,"live_emit",None): self.live_emit(kind,value)
            if kind == "motion":
                if active[0]: frames.append({"offsets":deepcopy(pose), "writes":list(writes)}); writes.clear()
                active[0] = True
            elif kind == "servo":
                servo, degrees = value
                if not math.isfinite(degrees) or abs(degrees)>150: raise ValueError("Out-of-range servo command")
                for name, finger in self.config.fingers.items():
                    if servo in finger.servos: pose[name][finger.servos.index(servo)] = degrees-finger.middle[finger.servos.index(servo)]
                writes.append([servo,degrees])
        if answer is not None:
            ChatHand(self.config, FastCancel(self.cancel), emit, ChatSession(), None).perform(answer)
        if active[0]: frames.append({"offsets":deepcopy(pose), "writes":list(writes)})
        return frames

    def pose_error(self, actual, expected, frozen_tips=None):
        motor_error = max(abs(actual[n][i]-expected[n][i]) for n in FINGER_ORDER for i in (0,1))
        # Reset solver state for each configuration so comparison does not depend on prior trials.
        model_a = AmazingHandKinematics(self.config)
        model_b = AmazingHandKinematics(self.config)
        a,_,_ = model_a.points_for_pose(actual)
        b,_,_ = model_b.points_for_pose(expected)
        errors = {n: float(np.linalg.norm(a[f"finger{i}_tip"]-b[f"finger{i}_tip"])*1000)
                  for i,n in enumerate(FINGER_ORDER,1)}
        if frozen_tips is not None:
            errors = {n:float(np.linalg.norm(a[f"finger{i}_tip"]-np.asarray(frozen_tips[n]))*1000)
                      for i,n in enumerate(FINGER_ORDER,1)}
        finite = all(math.isfinite(e) for e in errors.values())
        def diagnostics(model, targets):
            deltas=[]
            for i,name in enumerate(FINGER_ORDER,1):
                for j in (0,1):
                    actual_angle=float(np.rad2deg(model.data.joint(f"finger{i}_motor{j+1}").qpos[0]))
                    requested=targets[name][j]*self.config.fingers[name].sim_signs[j]
                    deltas.append(abs(actual_angle-requested))
            mask=model.data.efc_type==model.mujoco.mjtConstraint.mjCNSTR_EQUALITY
            closure=float(np.max(np.abs(model.data.efc_pos[mask]))) if np.any(mask) else 0.
            return {"max_motor_clipping_deg":max(deltas),"max_closure_residual_m":closure}
        actual_diagnostics=diagnostics(model_a,actual)
        reference_diagnostics=diagnostics(model_b,expected)
        valid=finite and all(d["max_motor_clipping_deg"]<=0.1 and d["max_closure_residual_m"]<=1e-7
                             for d in (actual_diagnostics,reference_diagnostics))
        return {"max_motor_target_error_deg":motor_error, "modeled_fingertip_errors_mm":errors,
                "finite_model_coordinates":finite,"actual_solver":actual_diagnostics,"reference_solver":reference_diagnostics,
                "pose_match":valid and motor_error<=self.motor_tolerance and max(errors.values())<=self.tip_tolerance}

    def evaluate(self, case, answer, row):
        if row["outcome"] in ("error","cancelled"):
            return {"status":"not_evaluated", "reason":row["outcome"]}
        frozen = self.frozen_cases[case["id"]] if self.frozen_cases is not None else None
        if frozen and frozen["case_sha256"] != digest(case):
            raise ValueError("Command/reference changed after validation freeze: " + case["id"])
        expected = [f["offsets"] for f in frozen["frames"]] if frozen else self.expected_frames(case)
        actual = self.capture(answer, frozen["initial_offsets"] if frozen else self.initial(case))
        if not expected:
            return {"status":"evaluated", "actual_frames":actual, "no_motion":not actual,
                    "behavior_match":not actual and row["outcome"] in ("correct","unresolved","rejected"),
                    "criterion":"No motion; clarification wording is not semantically scored"}
        errors = [self.pose_error(a["offsets"],b, frozen["frames"][i]["modeled_tips_m"] if frozen else None)
                  for i,(a,b) in enumerate(zip(actual,expected))]
        no_checks = []
        dynamic_errors = []
        expected_plan = frozen["expected_plan"] if frozen else signature(expected_answer(case["expected"]))
        for index, step in enumerate(expected_plan):
            if step["action"] != "no" or index>=len(actual): continue
            finger=self.config.fingers["index"]
            observed=[(servo,deg-finger.middle[finger.servos.index(servo)]) for servo,deg in actual[index]["writes"] if servo in finger.servos]
            pairs=frozen["no_index_trace_offsets"] if frozen else [[-40,40]] + [[-10,80],[-80,10]]*3 + [[-35,35]]
            desired=[(servo,deg) for pair in pairs for servo,deg in zip(finger.servos,pair)]
            no_checks.append(len(observed)==len(desired) and all(a[0]==b[0] and abs(a[1]-b[1])<=self.motor_tolerance for a,b in zip(observed,desired)))
            if frozen and len(observed)==len(desired):
                for phase, reference in enumerate(frozen["dynamic_frames"][str(index)]):
                    pose=deepcopy(actual[index]["offsets"])
                    pose["index"]=[observed[phase*2][1],observed[phase*2+1][1]]
                    dynamic_errors.append(self.pose_error(pose,reference["offsets"],reference["modeled_tips_m"]))
        return {"status":"evaluated", "actual_frames":actual, "expected_step_count":len(expected),
                "actual_step_count":len(actual), "step_errors":errors, "no_gesture_trace_checks":no_checks,
                "dynamic_pose_errors":dynamic_errors,
                "pose_sequence_match":len(actual)==len(expected) and all(e["pose_match"] for e in errors+dynamic_errors) and all(no_checks),
                "action_plan_match":row["outcome"]=="correct"}

    def check_reference_execution(self, item, catalog):
        frozen=self.frozen_controller[item["id"]] if self.frozen_controller is not None else None
        if frozen and frozen["reference_sha256"]!=digest(item):
            raise ValueError("Controller reference changed after validation freeze")
        pose=self.initial({})
        observed=[]
        writes=[]
        active=[False]
        def emit(kind,value):
            if kind=="motion" or (kind=="status" and value.startswith("Playing ")):
                if active[0]: observed.append(deepcopy(pose))
                active[0]=True
            elif kind=="servo":
                servo,degree=value
                writes.append([servo,degree])
                for name,finger in self.config.fingers.items():
                    if servo in finger.servos:
                        i=finger.servos.index(servo); pose[name][i]=degree-finger.middle[i]
        play_reference(item,catalog,self.config,FastCancel(self.cancel),emit,None)
        if active[0]: observed.append(deepcopy(pose))
        if item["kind"]=="pose": expected=[item["offsets"]]
        elif item["kind"]=="sequence": expected=[next(p["offsets"] for p in catalog if p["id"]==key) for key in item["poses"]]
        else: expected=self.expected_frames({"expected":item["expected"]})
        if frozen: expected=[f["offsets"] for f in frozen["frames"]]
        errors=[self.pose_error(a,b,frozen["frames"][i]["modeled_tips_m"] if frozen else None)
                for i,(a,b) in enumerate(zip(observed,expected))]
        trace_match=True
        dynamic_errors=[]
        if item["kind"]=="gesture" and item.get("expected",{}).get("steps")==[{"action":"no"}]:
            finger=self.config.fingers["index"]
            pairs=[[-40,40]]+[[-10,80],[-80,10]]*3+[[-35,35]]
            if frozen: pairs=[frame["offsets"]["index"] for frame in frozen["dynamic_frames"]]
            observed_trace=[(servo,degree-finger.middle[finger.servos.index(servo)]) for servo,degree in writes if servo in finger.servos]
            desired_trace=[(servo,degree) for pair in pairs for servo,degree in zip(finger.servos,pair)]
            trace_match=len(observed_trace)==len(desired_trace) and all(a[0]==b[0] and abs(a[1]-b[1])<=self.motor_tolerance for a,b in zip(observed_trace,desired_trace))
            if frozen and len(observed_trace)==len(desired_trace):
                for phase,reference in enumerate(frozen["dynamic_frames"]):
                    actual=deepcopy(observed[-1]); actual["index"]=[observed_trace[phase*2][1],observed_trace[phase*2+1][1]]
                    dynamic_errors.append(self.pose_error(actual,reference["offsets"],reference["modeled_tips_m"]))
        return {"id":item["id"],"kind":"controller_reference_reproduction","observed_frames":observed,
                "expected_step_count":len(expected),"actual_step_count":len(observed),"step_errors":errors,
                "dynamic_trace_match":trace_match,
                "dynamic_pose_errors":dynamic_errors,
                "passed":len(expected)==len(observed) and all(e["pose_match"] for e in errors+dynamic_errors) and trace_match}


def freeze_validation(cases, protocol, cancel=None):
    """Materialize every expected step before interpretation; no LLM/parser calls."""
    cancel=cancel or threading.Event()
    validate_dataset(cases)
    config=HandConfig.from_dict(protocol["hand_config"])
    evaluator=PoseEvaluator(config,reference_library(config,protocol),cancel)
    cache={}
    def frame(pose):
        from .experiment_protocol import validate_pose
        validate_pose(pose,config)
        key=digest(pose)
        if key not in cache:
            check=evaluator.pose_error(pose,pose)
            if not check["pose_match"]: raise ValueError("Reference violates model bounds or closure constraints")
            model=AmazingHandKinematics(config)
            points,_,_=model.points_for_pose(pose)
            cache[key]={"offsets":deepcopy(pose),
                        "absolute_servo_targets_deg":{str(servo):config.fingers[n].middle[i]+pair[i]
                              for n,pair in pose.items() for i,servo in enumerate(config.fingers[n].servos)},
                        "modeled_tips_m":{n:points[f"finger{i}_tip"].tolist() for i,n in enumerate(FINGER_ORDER,1)},
                        "solver_check":check["reference_solver"]}
        return deepcopy(cache[key])
    frozen={}
    for case in cases:
        if cancel.is_set():
            from amazinghand.chat.cancellation import Cancelled
            raise Cancelled()
        poses=evaluator.expected_frames(case)
        frame(evaluator.initial(case))
        frozen[case["id"]]={"case_sha256":digest(case), "category":case["category"],
            "criterion":"pose_sequence" if poses else "no_motion",
            "initial_offsets":evaluator.initial(case),"frames":[frame(p) for p in poses],
            "expected_plan":signature(expected_answer(case["expected"])),
            "no_index_trace_offsets":[[-40,40]]+[[-10,80],[-80,10]]*3+[[-35,35]]}
        frozen[case["id"]]["dynamic_frames"]={}
        for i,step in enumerate(frozen[case["id"]]["expected_plan"]):
            if step["action"]=="no":
                frozen[case["id"]]["dynamic_frames"][str(i)]=[frame(dict(deepcopy(poses[i]),index=pair))
                    for pair in frozen[case["id"]]["no_index_trace_offsets"]]
    controller={}
    for item in protocol["items"]:
        if item["kind"]=="pose": poses=[item["offsets"]]
        elif item["kind"]=="sequence": poses=[next(p["offsets"] for p in protocol["items"] if p["id"]==key) for key in item["poses"]]
        else: poses=evaluator.expected_frames({"expected":item["expected"]})
        controller[item["id"]]={"reference_sha256":digest(item),"frames":[frame(p) for p in poses]}
        if item["kind"]=="gesture" and item.get("expected",{}).get("steps")==[{"action":"no"}]:
            controller[item["id"]]["dynamic_frames"]=[frame(dict(deepcopy(poses[-1]),index=pair))
                for pair in [[-40,40]]+[[-10,80],[-80,10]]*3+[[-35,35]]]
    result={"version":2,"input_sha256":digest({"cases":cases,"protocol":protocol}),
            "cases":frozen,"controller_references":controller,
            "persistence":{"checks":10,"expected_configuration_sha256":digest(asdict(config))},
            "coverage":{"command_cases":len(frozen),"movement_cases":sum(bool(r["frames"]) for r in frozen.values()),
                        "no_motion_cases":sum(not r["frames"] for r in frozen.values()),
                        "command_step_poses":sum(len(r["frames"]) for r in frozen.values()),
                        "dynamic_phase_poses":sum(len(frames) for r in frozen.values() for frames in r["dynamic_frames"].values()),
                        "controller_cases":len(controller),"unique_configurations":len(cache)},
            "scope":"Evaluator-only specifications. Hidden from interpreter input, not encrypted. Modeled fingertips are not physical measurements."}
    result["reference_sha256"]=digest(result)
    return result


def validation_for_run(cases, protocol, cancel):
    for folder in ("four_finger_200", "validation"):
        prepared=ROOT/"configs"/"experiments"/folder/"validation_references.json"
        if prepared.exists():
            bundle=json.loads(prepared.read_text(encoding="utf-8"))
            if bundle.get("version")==2 and bundle.get("input_sha256")==digest({"cases":cases,"protocol":protocol}):
                if bundle.get("reference_sha256")!=digest({k:v for k,v in bundle.items() if k!="reference_sha256"}):
                    raise ValueError("Saved validation references failed their integrity check.")
                return bundle
    return freeze_validation(cases,protocol,cancel)


def run_complete_study(dataset, protocol, models, repetitions, seed, settings, store, cancel, emit,
                       motor_tolerance=1.0, fingertip_tolerance_mm=2.0, physical=None, include_deterministic=True):
    config=HandConfig.from_dict(protocol["hand_config"])
    references=reference_library(config,protocol)
    cases=validate_dataset(complete_cases(dataset))
    store.write("stimuli.json", [{k:c[k] for k in ("id","category","command","targets","previous") if k in c} for c in cases])
    store.write("evaluator_references.json", {"library":references,"cases":cases,"protocol":protocol,
        "motor_tolerance_deg":motor_tolerance,"fingertip_tolerance_mm":fingertip_tolerance_mm,
        "interpretation_access":"Only command, initial targets and previous conversational context are passed to the interpreter"})
    evaluator=PoseEvaluator(config,references,cancel,motor_tolerance,fingertip_tolerance_mm)
    try:
        frozen=validation_for_run(cases,protocol,cancel)
        store.write("validation_references.json",frozen)
        evaluator.frozen_cases=frozen["cases"]
        evaluator.frozen_controller=frozen["controller_references"]
        from .experiment_capture import TrialCaptures
        captures=TrialCaptures(store.path,config,models)
        evaluator.live_emit=emit if physical is None else None
        def prepare(case):
            from .assistant_engine import CommandController
            from .gestures import AmazingHandGestures
            initial=evaluator.initial(case)
            if physical is not None:
                gestures=AmazingHandGestures(CommandController(config,cancel,emit,physical))
                gestures.torque_on()
                for name,pair in initial.items():gestures.move_finger(name,*pair,speed=3)
                if cancel.wait(1.5):
                    from amazinghand.chat.cancellation import Cancelled
                    raise Cancelled()
            else:
                for name,pair in initial.items():
                    for j,servo in enumerate(config.fingers[name].servos):
                        emit("servo",(servo,pair[j]+config.fingers[name].middle[j]))
            return {"commanded_offsets":initial,"backend":"hardware" if physical is not None else "kinematics",
                    "settle_wait_seconds":1.5 if physical is not None else 0,"measured_completion":False}
        def execute(answer):
            import time
            start=time.perf_counter()
            result={"execution_backend":"hardware","controller_completed":False,"physical_observation":"not assessed","servo_targets":[]}
            def mirror(kind,value):
                if kind=="servo":result["servo_targets"].append({"servo":value[0],"degrees":value[1]})
                emit(kind,value)
            try:
                if motion_plan(answer):
                    ChatHand(config,cancel,mirror,ChatSession(),physical).perform(answer)
                    result["controller_completed"]=True
            except Exception as exc:result["execution_error"]=str(exc) or type(exc).__name__
            result["execution_seconds"]=time.perf_counter()-start
            return result
        def evaluate_and_capture(case,answer,row):
            evaluation=evaluator.evaluate(case,answer,row)
            row["captures"]=captures.save(case,row,evaluation,evaluator.initial(case))
            return evaluation
        emit("status","Running interpretation and robustness comparisons with hidden-reference evaluation…")
        run_matrix(cases,models,repetitions,seed,settings,store,cancel,emit,
                   evaluate=evaluate_and_capture,prepare=prepare,execute=execute if physical is not None else None,
                   include_deterministic=include_deterministic)
        if cancel.is_set(): return
        emit("status","Checking frozen pose configurations and save/reload consistency…")
        persistence=[]
        from .config import load_config, save_config
        for index in range(10):
            if cancel.is_set(): return
            path=store.path/"configuration_roundtrip.json"
            save_config(config,path)
            restored=load_config(path)
            persistence.append({"attempt":index+1,"fields_equal":asdict(restored)==asdict(config)})
        store.write("persistence_checks.json",persistence)
        pose_checks=[]
        for item in protocol["items"]:
            if cancel.is_set(): return
            pose_checks.append(evaluator.check_reference_execution(item,protocol["items"]))
        store.write("reference_execution_checks.json",pose_checks)
    finally:
        import sys
        from .experiment_report import make_charts, write_report, export_evaluations
        completed=json.loads((store.path/"completion.json").read_text()) if (store.path/"completion.json").exists() else {}
        state={"cancelled":cancel.is_set(),"matrix":completed,
               "error":str(sys.exc_info()[1]) if sys.exc_info()[1] else "",
               "evaluation_errors":sum(r.get('evaluation',{}).get('status')=='error' for r in store.rows)}
        store.write("study_completion.json",state)
        (store.path/"trials.jsonl").touch(exist_ok=True)
        emit("status","Exporting report, charts and recorded data…")
        errors={}
        for name,export in (("tables",store.export),("evaluations",lambda:export_evaluations(store)),("charts",lambda:make_charts(store.path)),("report",lambda:write_report(store.path))):
            try: export()
            except Exception as exc: errors[name]=str(exc)
        state.update(report_created=(store.path/"report.md").exists(),
                     charts_created=all((store.path/f"fig_experiment_{name}.{ext}").exists() for name in ("outcomes","latency") for ext in ("png","svg")),
                     export_errors=errors)
        store.write("study_completion.json",state)
        if errors: emit("status","Run saved; export issue: "+str(errors))
        else: emit("status",f"Report and charts exported automatically to {store.path}")
