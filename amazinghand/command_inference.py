"""Typed command output contract. No benchmark cases or reference targets."""
from copy import deepcopy

INFERENCE_VERSION = "typed-actions-1.0"
FINGERS = ("index", "middle", "ring", "thumb")
TIMING = {"speed": {"type": "integer", "minimum": 1, "maximum": 7},
          "hold_seconds": {"type": "number", "minimum": .2, "maximum": 5}}


def branch(action, fields=None):
    properties = {"action": {"type": "string", "enum": action if isinstance(action,list) else [action]},
                  **deepcopy(TIMING), **(fields or {})}
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


COMMAND_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string", "maxLength": 1200},
    "steps": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"anyOf": [
        branch("pose", {"fingers": {"type": "object", "properties": {
            f: {"type": "number", "minimum": 0, "maximum": 100} for f in FINGERS},
            "minProperties": 1, "additionalProperties": False}}),
        branch("math", {"expression": {"type": "string", "minLength": 1, "maxLength": 80}}),
        branch("number", {"number": {"type": "integer", "minimum": 0, "maximum": 20}}),
        branch(["yes", "no", "peace", "open", "close", "point", "ok", "wave", "relax", "unknown"]),
    ]}},
    "repeat": {"type": "integer", "minimum": 1, "maximum": 3}},
    "required": ["answer", "steps", "repeat"], "additionalProperties": False}

COMMAND_PROMPT = """Translate the user's request into an exact robot-hand command plan.
The robot has FOUR digits: index, middle, ring, thumb. No pinky/little finger,
wrist, arm, camera, or force sensor. State supplied below is COMMANDED state.
Your JSON steps, not your prose, control the hand. Never substitute a generic
gesture for a requested finger movement. Give a short answer and the full plan.

CHOOSE THE ACTION TYPE:
- pose: any explicit finger selection or numeric opening. fingers contains
  ONLY the digits changed in that step. Values are ABSOLUTE opening percentages:
  0 closed, 100 open. Use pose even at 0 or 100; never whole-hand open/close for
  a selective finger request. Unmentioned fingers stay unchanged.
- open/close: named whole-hand open/closed gestures.
- point: requested pointing gesture (index extended, other three curled).
- peace: requested V/victory gesture, index and middle separated and extended.
- yes: requested thumb-up/thumb-extension gesture, other three curled.
- no: requested no gesture, three index-wag cycles. One no step already performs
  the three cycles: do not repeat it three times.
- wave: requested greeting gesture implemented as open-close-open.
- ok: requested predefined OK shape; this does not guarantee contact.
- relax: requested torque release.
- math: requested arithmetic. Supply expression using digits,+,-,*,/,parentheses;
  the controller computes the result. Do not return a gesture that merely means
  explanation, and do not omit expression. Keep operator precedence exactly.
- number: a requested integer count from 0 through 20. The controller handles
  groups of four and separators; do not manually expand them. 0 is closed.
- unknown: clarification, unsupported action, impossible request, or no motion.
  Ordinary factual prose should use unknown rather than inventing movement.
  An explicitly requested yes/no factual response may use yes or no.

STATE AND ORDER:
For 'to' use the specified absolute opening. 'Open/increase by d' means current
opening+d; 'close/decrease by d' means current opening-d. Use the supplied state
and prior single-finger command to resolve pronouns. Missing or ambiguous state
or reference => unknown. Do not assume a starting opening or clip an invalid value.
For sequences, update a temporary state after EACH step. Named open sets all
openings to 100; close to 0; point/no index=100 others=0; yes thumb=100 others=0;
peace index=middle=100 others=0. OK and relax leave the symbolic openings unknown.
Multiple fingers requested together belong in ONE pose step. 'Then', 'followed
by', or explicitly ordered targets require separate steps. Keep order and all
intermediate targets. Repeat applies to the entire pattern; do not duplicate it.

TIMING AND VALIDITY:
Every step supplies speed and hold_seconds. Defaults: speed=3, hold_seconds=1.5.
If the user specifies timing/speed for each step, apply it to each step. Preserve
individual step parameters. Speed must be integer 1..7, hold 0.2..5 seconds,
opening 0..100, repeat 1..3, up to 8 steps, 24 expanded movements and 45 seconds.
If ANY requested part is invalid, requires absent anatomy, or needs clarification,
return ONE unknown step for the ENTIRE request. Do not execute a valid prefix,
silently drop a constraint, or substitute another body part. Negated movement
requests must not become the forbidden action.

EXAMPLES (the numbers are illustrative, not preset values):
Input: Set thumb to 46 percent, leaving other digits unchanged.
Output: {"answer":"Setting thumb opening.","steps":[{"action":"pose","fingers":{"thumb":46},"speed":3,"hold_seconds":1.5}],"repeat":1}
Input: Middle is commanded at 52%; reduce only middle by 7 percentage points.
Output: {"answer":"Middle target is 45%.","steps":[{"action":"pose","fingers":{"middle":45},"speed":3,"hold_seconds":1.5}],"repeat":1}
Input: First point for 0.8 seconds, then close for 1.2 seconds at speed 4.
Output: {"answer":"Point, then close.","steps":[{"action":"point","speed":3,"hold_seconds":0.8},{"action":"close","speed":4,"hold_seconds":1.2}],"repeat":1}
Input: What is the product of seven and two?
Output: {"answer":"Calculating the product.","steps":[{"action":"math","expression":"7*2","speed":3,"hold_seconds":1.5}],"repeat":1}
"""


def validate_action_fields(data):
    """Reject contradictory fields instead of silently discarding their intent."""
    if not isinstance(data,dict) or not isinstance(data.get("steps"),list):
        raise ValueError("A command response requires a steps array.")
    for step in data["steps"]:
        if not isinstance(step,dict):raise ValueError("Each command must be an object.")
        action=step.get("action")
        relevant={"pose":"fingers", "math":"expression", "number":"number"}.get(action)
        if relevant and step.get(relevant) is None:
            raise ValueError(f"{action} requires {relevant}.")
        for name in ("fingers","expression","number"):
            if name!=relevant and step.get(name) is not None:
                raise ValueError(f"{name} conflicts with action {action}; no movement.")
