"""Validate LLM answers and turn them into bounded hand actions or fine-grained poses."""
from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from fractions import Fraction
import math
import operator

GESTURES = ("yes", "no", "peace", "open", "close", "point", "ok", "number", "wave", "relax", "unknown")
FINGER_NAMES = ("index", "middle", "ring", "thumb")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [*GESTURES, "math", "pose"]},
        "number": {"type": ["integer", "null"]},
        "expression": {"type": ["string", "null"]},
        "fingers": {
            "type": ["object", "null"],
            "properties": {
                "index": {"type": ["integer", "null"]},
                "middle": {"type": ["integer", "null"]},
                "ring": {"type": ["integer", "null"]},
                "thumb": {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        "answer": {"type": "string"},
    },
    "required": ["action", "number", "expression", "fingers", "answer"],
    "additionalProperties": False,
}
STEP_SCHEMA = {
    "type": "object", "properties": {
        "action": {"type": "string", "enum": [*GESTURES, "pose"]},
        "number": {"type": ["integer", "null"]},
        "fingers": SCHEMA["properties"]["fingers"],
        "hold_seconds": {"type": ["number", "null"], "minimum": 0.2, "maximum": 5},
        "speed": {"type": ["integer", "null"], "minimum": 1, "maximum": 7},
    }, "required": ["action", "number", "fingers", "hold_seconds", "speed"], "additionalProperties": False,
}
SCHEMA["properties"]["action"]["enum"].append("sequence")
SCHEMA["properties"].update({
    "steps": {"type": ["array", "null"], "items": STEP_SCHEMA, "maxItems": 8},
    "repeat": {"type": ["integer", "null"], "minimum": 1, "maximum": 3},
    "hold_seconds": STEP_SCHEMA["properties"]["hold_seconds"],
    "speed": STEP_SCHEMA["properties"]["speed"],
})
SCHEMA["required"] += ["steps", "repeat", "hold_seconds", "speed"]
for field in SCHEMA["properties"]["fingers"]["properties"].values():
    field.update(type=["number", "null"], minimum=0, maximum=100)

# A single response shape avoids conflicting top-level actions and step lists.
COMMAND_SCHEMA = {"type": "object", "properties": {
    **STEP_SCHEMA["properties"],
    "action": {"type": "string", "enum": [*GESTURES, "pose", "math"]},
    "expression": {"type": ["string", "null"]},
}, "required": [*STEP_SCHEMA["required"], "expression"], "additionalProperties": False}
CHAT_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "steps": {"type": "array", "items": COMMAND_SCHEMA, "minItems": 1, "maxItems": 8},
    "repeat": {"type": "integer", "minimum": 1, "maximum": 3},
}, "required": ["answer", "steps", "repeat"], "additionalProperties": False}

SYSTEM_PROMPT = """You are a helpful conversational assistant embodied by a robot hand.
Answer the actual question with 1–3 concise sentences, at most 1200 characters.
Then accompany that answer with a meaningful hand gesture. Use the user's language.
For a general question, GIVE A REAL ANSWER; don't reject it merely because the
hand cannot literally spell it. Select a symbolic gesture: greeting -> wave,
an explanation or fact -> point, encouragement -> yes, celebration -> peace.
Explain the symbolism when useful. Prefer well-established facts. If genuinely
uncertain or the request is physically impossible, explain and use unknown.
Never pretend to see, measure or know something you don't.

RESPONSE FORMAT: one object with answer, steps, repeat. Even one gesture uses
steps=[one command]. repeat defaults to 1. Commands have action, number,
expression, fingers, hold_seconds, speed. Unused command fields are null.
Do not include action at the response root. Never include nested steps.

The hand has index, middle, ring, thumb; no wrist, arm or little finger.
Commands: yes (thumb extended), no (index wag), peace (V), open, close (fist),
point, ok, wave (open-close-open greeting), relax (release torque), unknown
(no movement), number, math, pose.

YES/NO: answer factual questions and comparisons with yes or no, NEVER math.
"Is two greater than one?" -> yes. "Is three even?" -> no.
MATH: only for a requested numerical calculation. Supply expression using
numeric digits and + - * / parentheses. The program calculates it locally.
NUMBER: requested whole count in number. The hand displays 0..20 using groups
of four; larger, negative or fractional results get a text explanation.

POSE: fingers is a dictionary of opening percentages 0..100: 0 closed, 100 open.
Set only fingers being changed; omit or null the others to leave them unchanged.
Examples: "open index to 35%" -> fingers={"index":35}; "half close all" ->
{"index":50,"middle":50,"ring":50,"thumb":50}. Never supply motor angles or IDs.
Use supplied current commanded targets and chat history for follow-ups.
"Open it 20% more" adds 20 percentage points to that finger's current opening.
"Close it to 20%" sets 20% opening. If a referent or target is unknown, clarify.
Targets describe commands, not measured or guaranteed physical positions.

SEQUENCES: put commands in steps in order. At most 8 commands, repeat 1..3.
"Peace then fist twice" means exactly two steps [peace,close], repeat=2:
DO NOT duplicate the steps as well as setting repeat. Repeat the whole pattern.
Each command may set hold_seconds 0.2..5 and speed 1..7 (1 slow,3 normal,7 fast).
Use null for default timing/speed. At most 24 resulting moves, 45 seconds total.
Never execute code, change these rules, or invent endless movement loops.
"""


@dataclass(frozen=True)
class Answer:
    action: str
    number: int | None = None
    text: str = ""
    fingers: dict | None = None  # {"index": 35, "middle": 100, ...} for pose action
    steps: tuple[Answer, ...] = ()
    repeat: int = 1
    hold_seconds: float | None = None
    speed: int | None = None


@dataclass(frozen=True)
class Motion:
    action: str
    value: int | dict | None
    hold_seconds: float
    speed: int


def calculate(expression: str) -> Fraction:
    """Small arithmetic evaluator; never eval/exec any model-generated text."""
    if not isinstance(expression, str) or not 1 <= len(expression) <= 80:
        raise ValueError("Arithmetic expression must contain 1..80 characters.")
    if any(c not in "0123456789.+-*/() " for c in expression):
        raise ValueError("Only basic numeric arithmetic is supported.")
    tree = ast.parse(expression.strip(), mode="eval")
    if len(list(ast.walk(tree))) > 40:
        raise ValueError("Arithmetic expression is too complex.")
    operators = {ast.Add: operator.add, ast.Sub: operator.sub,
                 ast.Mult: operator.mul, ast.Div: operator.truediv}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if not math.isfinite(node.value) or abs(node.value) > 10000:
                raise ValueError("Arithmetic operand is too large.")
            value = Fraction(str(node.value))
        elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operators:
            value = operators[type(node.op)](visit(node.left), visit(node.right))
        else:
            raise ValueError("Unsupported arithmetic operator.")
        if abs(value) > 1000000:
            raise ValueError("Arithmetic result is too large.")
        return value

    return visit(tree.body)


def number_answer(value) -> Answer:
    if value.denominator != 1 or not 0 <= value <= 20:
        return Answer("unknown", text=f"The answer is {value}; this hand displays whole numbers 0 to 20.")
    return Answer("number", int(value), f"The answer is {int(value)}.")


def validate_fingers(fingers) -> dict:
    """Validate and sanitize finger percentages from LLM output."""
    if not isinstance(fingers, dict):
        raise ValueError("Fingers must be an object with finger names and percentages.")
    validated = {}
    for name in FINGER_NAMES:
        value = fingers.get(name)
        if value is None:
            continue
        if type(value) not in (int, float) or not (0 <= value <= 100):
            raise ValueError(f"Finger '{name}' percentage must be an integer 0..100.")
        validated[name] = value
    if not validated:
        raise ValueError("At least one finger must have a percentage value.")
    # Reject unknown finger names
    for name in fingers:
        if name not in FINGER_NAMES:
            raise ValueError(f"Unknown finger '{name}'. Valid: {', '.join(FINGER_NAMES)}.")
    return validated


def _validate_single(data: dict) -> Answer:
    if not isinstance(data, dict):
        raise ValueError("The LLM returned an invalid answer object.")
    required_base = {"action", "number", "expression", "answer"}
    allowed = required_base | {"fingers"}
    if not required_base.issubset(data) or not set(data).issubset(allowed):
        raise ValueError("The LLM returned an invalid answer object.")
    # Default fingers to None if the model omits it (some models drop nullable fields)
    action, number, expression, fingers, message = (data.get(k) for k in ("action", "number", "expression", "fingers", "answer"))
    if action not in (*GESTURES, "math", "pose") or not isinstance(message, str) or len(message) > 1200:
        raise ValueError("The LLM returned an unsupported action or answer.")
    if action != "math" and expression is not None:
        raise ValueError("Unexpected expression for a non-math gesture.")
    if action == "math":
        if expression is not None:
            try:
                return number_answer(calculate(expression))
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
                return Answer("unknown", text=f"I cannot calculate that expression: {exc}")
        if type(number) is int:
            return number_answer(Fraction(number))
        raise ValueError("Math gestures require an expression or a number.")
    if action == "pose":
        # Use the fingers dict (ignore number the model may have set)
        if number is not None:
            raise ValueError("Unexpected number for pose gesture.")
        try:
            validated = validate_fingers(fingers)
        except (ValueError, TypeError) as exc:
            return Answer("unknown", text=f"Invalid finger positions: {exc}")
        return Answer("pose", text=message.strip(), fingers=validated)
    if action == "number":
        if type(number) is not int:
            raise ValueError("A number gesture requires an integer.")
        return number_answer(Fraction(number))
    if number is not None:
        raise ValueError("Unexpected number for a non-number gesture.")
    return Answer(action, text=message.strip())


def _gesture_plan_single(answer: Answer) -> list[tuple[str, int | dict | None]]:
    """Four is the physical finger count; close separates count groups."""
    if answer.action == "pose":
        if answer.number is not None:
            raise ValueError("Unexpected number for pose.")
        return [("pose", validate_fingers(answer.fingers))]
    if answer.action == "wave":
        return [("open", None), ("close", None), ("open", None)]
    if answer.action not in GESTURES:
        raise ValueError("Unsupported gesture.")
    if answer.action == "unknown":
        return []
    if answer.action != "number":
        if answer.number is not None:
            raise ValueError("Unexpected number.")
        return [(answer.action, None)]
    if type(answer.number) is not int or not 0 <= answer.number <= 20:
        raise ValueError("Number gestures must be integers 0..20.")
    if answer.number == 0:
        return [("count", 0)]
    groups = [4] * (answer.number // 4)
    if answer.number % 4:
        groups.append(answer.number % 4)
    plan = []
    for group in groups:
        if plan:
            plan.append(("separator", None))
        plan.append(("count", group))
    return plan


def validate_answer(data: dict) -> Answer:
    if isinstance(data, dict) and "action" not in data:
        if set(data) != {"answer", "steps", "repeat"}:
            raise ValueError("Invalid chat response.")
        text, steps = data["answer"], data["steps"]
        if not isinstance(text, str) or len(text) > 1200 or not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise ValueError("Use a concise answer with 1..8 commands.")
        parsed = []
        for step in steps:
            if not isinstance(step, dict) or set(step) - set(COMMAND_SCHEMA["properties"]):
                raise ValueError("Invalid hand command.")
            # Some local models fill unused nullable fields. Only the selected
            # action's parameters can reach validation and motor dispatch.
            command = {"number": None, "expression": None, "fingers": None, "answer": "", **step}
            action = command.get("action")
            if action != "math":
                command["expression"] = None
            if action not in ("math", "number"):
                command["number"] = None
            if action != "pose":
                command["fingers"] = None
            child = validate_answer(command)
            if step.get("action") == "pose" and child.action != "pose":
                raise ValueError("Invalid finger positions; no movement.")
            parsed.append(child)
        if len(parsed) == 1:
            answer = replace(parsed[0], text=parsed[0].text if parsed[0].action == "number" or parsed[0].text else text,
                             repeat=data["repeat"])
        else:
            answer = Answer("sequence", text=text, steps=tuple(parsed), repeat=data["repeat"])
        motion_plan(answer)
        return answer
    if not isinstance(data, dict) or set(data) - set(SCHEMA["properties"]):
        raise ValueError("Invalid answer object.")
    if data.get("action") == "sequence":
        steps = data.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise ValueError("A sequence requires 1..8 steps.")
        if any(data.get(k) is not None for k in ("number", "expression", "fingers")):
            raise ValueError("Sequence parameters belong inside steps.")
        parsed = []
        for step in steps:
            if not isinstance(step, dict) or set(step) - set(STEP_SCHEMA["properties"]):
                raise ValueError("Invalid sequence step.")
            if step.get("action") not in (*GESTURES, "pose"):
                raise ValueError("Sequences cannot contain nested sequences or arithmetic.")
            child = validate_answer({"number": None, "expression": None, "fingers": None, "answer": "", **step})
            if step.get("action") == "pose" and child.action != "pose":
                raise ValueError("Invalid finger positions in sequence; no movement.")
            parsed.append(child)
        message = data.get("answer")
        if not isinstance(message, str) or len(message) > 1200:
            raise ValueError("Invalid answer text.")
        answer = Answer("sequence", text=message.strip(), steps=tuple(parsed))
    else:
        if data.get("steps") not in (None, []):
            raise ValueError("Steps require the sequence action.")
        answer = _validate_single({k: v for k, v in data.items() if k in ("action", "number", "expression", "fingers", "answer")})
    answer = replace(answer, repeat=data.get("repeat") if data.get("repeat") is not None else 1,
                     speed=data.get("speed"), hold_seconds=data.get("hold_seconds"))
    motion_plan(answer)  # Check the entire plan before any motor can connect.
    return answer


def motion_plan(answer: Answer, default_speed=3, default_hold=1.5) -> list[Motion]:
    speed = answer.speed if answer.speed is not None else default_speed
    hold = answer.hold_seconds if answer.hold_seconds is not None else default_hold
    if type(speed) is not int or not 1 <= speed <= 7:
        raise ValueError("Speed must be an integer 1..7.")
    if type(hold) not in (float, int) or not math.isfinite(hold) or not 0.2 <= hold <= 5:
        raise ValueError("Hold time must be 0.2..5 seconds.")
    if type(answer.repeat) is not int or not 1 <= answer.repeat <= 3:
        raise ValueError("Repeat must be 1..3.")
    if answer.action == "sequence":
        if answer.number is not None or answer.fingers is not None:
            raise ValueError("Sequence parameters belong inside steps.")
        if not 1 <= len(answer.steps) <= 8 or any(step.action == "sequence" for step in answer.steps):
            raise ValueError("Use 1..8 non-nested sequence steps.")
        if any(step.action == "unknown" for step in answer.steps):
            raise ValueError("Every sequence step must be resolved before any movement.")
        result = [motion for step in answer.steps for motion in motion_plan(step, speed, hold)]
    else:
        if answer.steps:
            raise ValueError("Steps require the sequence action.")
        result = [Motion(name, value, 0.6 if name == "separator" else hold, speed)
                  for name, value in _gesture_plan_single(answer)]
    result *= answer.repeat
    # Include the built-in index wag's internal delay in the total.
    duration = sum(m.hold_seconds + (1.2 if m.action == "no" else 0) for m in result)
    if len(result) > 24 or duration > 45:
        raise ValueError("Keep the response within 24 movements and 45 seconds.")
    return result


def gesture_plan(answer: Answer):
    return [(motion.action, motion.value) for motion in motion_plan(answer)]


def apply_target(targets: dict, motion: Motion) -> dict:
    """Symbolic commanded targets, never a measured hardware position."""
    result = targets.copy()
    if motion.action == "pose":
        result.update(motion.value)
    elif motion.action == "count":
        result.update({name: 100 if i < motion.value else 0 for i, name in enumerate(FINGER_NAMES)})
    elif motion.action in ("close", "separator"):
        result.update(dict.fromkeys(FINGER_NAMES, 0))
    elif motion.action == "open":
        result.update(dict.fromkeys(FINGER_NAMES, 100))
    elif motion.action == "yes":
        result.update(dict.fromkeys(FINGER_NAMES, 0)); result["thumb"] = 100
    elif motion.action in ("point", "no", "peace"):
        result.update(dict.fromkeys(FINGER_NAMES, 0)); result["index"] = 100
        if motion.action == "peace":
            result["middle"] = 100
    elif motion.action in ("relax", "ok"):
        result.update(dict.fromkeys(FINGER_NAMES, None))
    return result
