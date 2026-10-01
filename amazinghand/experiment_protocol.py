"""Frozen controller references derived from the application's gesture targets."""
from copy import deepcopy
from dataclasses import asdict
import math
import time

from .config import FINGER_ORDER
from .assistant_engine import CommandController
from .gestures import AmazingHandGestures
from amazinghand.chat.cancellation import Cancelled


def validate_pose(pose, config):
    if set(pose) != set(FINGER_ORDER):
        raise ValueError("A study pose must define all four fingers.")
    for name, pair in pose.items():
        if len(pair) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 90 for x in pair):
            raise ValueError(f"{name}: expected two finite offsets within -90..90 degrees.")
        if any(abs(x + mid) > 150 for x, mid in zip(pair, config.fingers[name].middle)):
            raise ValueError(f"{name}: calibrated target exceeds the controller's absolute range.")
    return pose


def study_protocol(config):
    opened = {n: [-35., 35.] for n in FINGER_ORDER}
    closed = {n: [90., -90.] for n in FINGER_ORDER}
    point = dict(deepcopy(closed), index=[-40., 40.])
    partial = {n: [27.5, -27.5] for n in FINGER_ORDER}  # 50% opening: 90 - 1.25*50
    yes = dict(deepcopy(closed), thumb=[-35., 35.])
    pinch = deepcopy(config.poses.get("pinched"))
    source = "saved pinched pose from current configuration"
    if pinch is None:
        pinch = deepcopy(closed)
        pinch["thumb"] = [75., 5.] if config.side == "left" else [0., -75.]
        source = "built-in pinched gesture; generated candidate"
    items = []
    for key, name, pose, origin in [
        ("G1", "Open", opened, "open_hand"), ("G2", "Closed", closed, "close_hand"),
        ("G3", "Point", point, "index_pointing"), ("G4", "Pinch", pinch, source),
        ("G5", "Partial grasp", partial, "50% opening for all fingers"),
        ("G6", "Yes", yes, "thumb extended, other fingers closed")]:
        items.append({"id": key, "name": name, "kind": "pose", "offsets": validate_pose(pose, config),
                      "source": origin, "hold_seconds": 1.5, "speed": 3, "suggested_trials": 10})
    items += [
        {"id": "G7", "name": "No", "kind": "gesture", "expected": {"steps": [{"action": "no"}]}, "suggested_trials": 10},
        {"id": "S1", "name": "Open–closed–open", "kind": "sequence", "poses": ["G1", "G2", "G1"], "suggested_trials": 10},
        {"id": "S2", "name": "Point–open", "kind": "sequence", "poses": ["G3", "G1"], "suggested_trials": 10}]
    return {"version": 1, "side": config.side, "hand_config": asdict(config), "items": items,
            "scope": "Commanded configurations; no force, contact, collision or physical success validation",
            "limits": {"offset_degrees": [-90, 90], "absolute_servo_degrees": [-150, 150],
                       "meaning": "software command bounds, not experimentally established mechanical limits"}}


def play_reference(item, catalog, config, cancel, emit, physical=None):
    """Identical calibrated targets in preview and hardware; no language inference."""
    if item["kind"] == "gesture":
        from .assistant_engine import ChatHand
        from .experiments import expected_answer
        from amazinghand.chat.chat_session import ChatSession
        ChatHand(config, cancel, emit, ChatSession(), physical).perform(expected_answer(item["expected"]))
        return
    poses = [item] if item["kind"] == "pose" else [next(p for p in catalog if p["id"] == key) for key in item["poses"]]
    # Validate the entire sequence before enabling torque.
    for pose in poses:
        validate_pose(pose["offsets"], config)
    controller = CommandController(config, cancel, emit, physical)
    gestures = AmazingHandGestures(controller)
    try:
        controller.check()
        gestures.torque_on()
        for pose in poses:
            emit("status", "Playing " + pose["id"] + " · " + pose["name"])
            for name, pair in pose["offsets"].items():
                gestures.move_finger(name, *pair, pose["speed"])
            if cancel.wait(pose["hold_seconds"]):
                raise Cancelled()
    finally:
        gestures.torque_free()
