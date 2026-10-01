"""Exact common hand controls bypass LLM interpretation; other requests use chat."""
from dataclasses import dataclass, replace
import re

if __package__:
    from .logic import Answer, FINGER_NAMES, apply_target, motion_plan, calculate, number_answer
else:
    from logic import Answer, FINGER_NAMES, apply_target, motion_plan, calculate, number_answer

ALIASES = {
    "peace": "peace", "show peace": "peace", "victory": "peace",
    "open": "open", "open hand": "open", "open the hand": "open", "open your hand": "open",
    "close": "close", "close hand": "close", "close the hand": "close", "close your hand": "close",
    "fist": "close", "make a fist": "close", "show a fist": "close",
    "wave": "wave", "wave hello": "wave", "say hello": "wave",
    "point": "point", "pointing": "point", "show ok": "ok", "ok sign": "ok",
    "thumbs up": "yes", "show yes": "yes", "show no": "no",
    "relax": "relax", "release hand": "relax", "release the hand": "relax", "stop moving": "relax",
}

PARSER_VERSION = "compositional-2.0"


@dataclass(frozen=True)
class ParseDecision:
    status: str  # matched, clarification, rejected, unmatched
    reason: str
    answer: Answer | None
    version: str = PARSER_VERSION


def parse_decision(text, targets, last_answer=None):
    """Classify before fallback; never execute or alter the supplied state."""
    try:
        answer = _parse_control(text, targets, last_answer)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
        return ParseDecision("rejected", str(exc), None)
    if answer is None:
        return ParseDecision("unmatched", "Outside the deterministic grammar; full request requires interpretation.", None)
    if answer.action == "unknown":
        return ParseDecision("clarification", answer.text, answer)
    return ParseDecision("matched", f"Validated {answer.action} plan with {len(motion_plan(answer))} movements.", answer)


def parse_control(text, targets, last_answer=None):
    decision = parse_decision(text, targets, last_answer)
    if decision.status == "rejected":
        raise ValueError(decision.reason)
    return decision.answer


def _last_finger(answer):
    if answer is None:
        return None
    if answer.action == "sequence" and answer.steps:
        return _last_finger(answer.steps[-1])
    if answer.fingers and len(answer.fingers) == 1:
        return next(iter(answer.fingers))
    return {"yes": "thumb", "point": "index", "no": "index"}.get(answer.action)


def _parse_control(text, targets, last_answer=None):
    text = text.lower().strip().strip(".!? ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b(?:percentage points?|percent)\b", "%", text)
    text = re.sub(r"^(?:(?:please|can you|could you|now)\s+)+", "", text)
    calculation = parse_calculation(text)
    if calculation is not None:
        return calculation
    repeat = 1
    match = re.search(r",?\s+(?:repeat\s+)?(twice|three times|\d+ times)$", text)
    if match:
        repeat = {"twice": 2, "three times": 3}.get(match[1]) or int(match[1].split()[0])
        text = text[:match.start()].strip()
    # Only recognize a sequence if EVERY part is an explicit control.
    parts = re.split(r"\s*(?:,?\s+and then\s+|,?\s+then\s+|\s*;\s*|\s+and (?=(?:open|close|set|move)\b))", text)
    if len(parts) > 1:
        current = targets.copy()
        previous = last_answer
        steps = []
        for part in parts:
            answer = parse_control(part, current, previous)
            if answer is not None and answer.action == "unknown":
                return answer  # Atomic: never retain earlier executable steps.
            if answer is None or answer.action == "sequence":
                return None
            steps.append(answer)
            for motion in motion_plan(answer):
                current = apply_target(current, motion)
            previous = answer
        result = Answer("sequence", text=" → ".join(step.text for step in steps), steps=tuple(steps), repeat=repeat)
        motion_plan(result)
        return result
    speed, hold = None, None
    # Suffix modifiers can appear in either order, but must not conflict.
    for _ in range(3):
        match = re.search(r"\s+(?:hold\s+)?(?:for\s+)?([+-]?\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)$", text)
        if match:
            if hold is not None:
                raise ValueError("Specify hold duration only once.")
            hold = float(match[1]); text = text[:match.start()].strip()
            continue
        match = re.search(r"\s+(slowly|fast|(?:at\s+)?speed\s+[+-]?\d+(?:\.\d+)?)$", text)
        if match:
            if speed is not None:
                raise ValueError("Specify speed only once.")
            raw = {"slowly": 1, "fast": 7}.get(match[1])
            raw = raw if raw is not None else float(match[1].split()[-1])
            if int(raw) != raw:
                raise ValueError("Speed must be an integer 1..7.")
            speed = int(raw); text = text[:match.start()].strip()
            continue
        break
    if text in ALIASES:
        answer = Answer(ALIASES[text], text=text.capitalize() + ".")
    elif text in ("half close all", "half open all", "half close the hand", "half open the hand"):
        answer = Answer("pose", text="All fingers at 50% opening.", fingers=dict.fromkeys(FINGER_NAMES, 50))
    else:
        # Shared-verb assignments are one simultaneous pose, evaluated against
        # the same starting state. Explicit repeated verbs denote a sequence.
        compound = re.fullmatch(r"(open|close|set|move)\s+(.+)", text)
        clauses = re.split(r"\s+and\s+|,\s*", compound[2]) if compound else []
        if len(clauses) > 1:
            fingers = {}
            for clause in clauses:
                child = parse_control(compound[1] + " " + clause, targets, last_answer)
                if child is None:
                    return None
                if child.action == "unknown":
                    return child
                if child.action != "pose":
                    return None
                if set(fingers).intersection(child.fingers):
                    raise ValueError("A simultaneous pose must assign each finger only once.")
                fingers.update(child.fingers)
            answer = Answer("pose", text="Simultaneous finger targets.", fingers=fingers,
                            repeat=repeat, speed=speed, hold_seconds=hold)
            motion_plan(answer)
            return answer
        match = re.fullmatch(
            r"(open|close|set|move)\s+(?:only\s+)?(?:the\s+)?(index|middle|ring|thumb|all|all fingers|hand|it|that finger|rest|the rest)"
            r"(?:\s+finger)?(?:\s+(to|at|by))?(?:\s+([+-]?\d+(?:\.\d+)?)\s*%)?(?:\s+(more|less))?", text)
        if not match:
            return None
        verb, target, relation, percentage, modifier = match.groups()
        last = _last_finger(last_answer)
        if target in ("it", "that finger", "rest", "the rest") and last is None:
            return Answer("unknown", text="Which finger do you mean?")
        if target in ("it", "that finger"):
            target = last
        names = list(FINGER_NAMES) if target in ("all", "all fingers", "hand") else (
            [name for name in FINGER_NAMES if name != last] if target in ("rest", "the rest") else [target])
        if percentage is None:
            if relation or modifier or verb not in ("open", "close"):
                return None
            value = 100 if verb == "open" else 0
        else:
            value = float(percentage)
            if not 0 <= value <= 100:
                raise ValueError("Opening or relative magnitude must be 0..100 percent.")
        if relation in ("to", "at") and modifier:
            raise ValueError("Do not mix an absolute target with a relative modifier.")
        if relation == "by" and verb in ("set", "move"):
            return Answer("unknown", text="Specify open by or close by to give the relative direction.")
        fingers = {}
        for name in names:
            if relation == "by" or modifier:
                if targets.get(name) is None:
                    return Answer("unknown", text=f"Set an explicit opening for {name} first; its current target is unknown.")
                direction = -1 if verb == "close" else 1
                if modifier == "less":
                    direction *= -1
                fingers[name] = targets[name] + direction * value
            else:
                fingers[name] = value
        answer = Answer("pose", text="; ".join(f"{name} at {value:g}% opening" for name, value in fingers.items()) + ".", fingers=fingers)
    answer = replace(answer, repeat=repeat, speed=speed, hold_seconds=hold)
    motion_plan(answer)
    return answer


def parse_calculation(text):
    """Only unambiguous basic arithmetic bypasses the language model."""
    expression = re.sub(r"^(?:what is|what's|whats|how much is|calculate|compute)\s+", "", text)
    for word, symbol in (("multiplied by", "*"), ("divided by", "/"), ("plus", "+"),
                         ("minus", "-"), ("times", "*"), ("over", "/")):
        expression = re.sub(r"\b" + word + r"\b", symbol, expression)
    words = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    expression = re.sub(r"\b(?:" + "|".join(words) + r")\b", lambda m: str(words.index(m[0])), expression)
    if not re.fullmatch(r"[\d.\s()+*/-]{1,80}", expression) or not any(op in expression for op in "+-*/"):
        return None
    try:
        return number_answer(calculate(expression))
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"Unsupported arithmetic: {exc}") from exc
    except (ZeroDivisionError, OverflowError):
        return Answer("unknown", text="That calculation has no supported finite result.")
