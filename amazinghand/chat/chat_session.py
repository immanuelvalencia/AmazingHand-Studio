"""Shared conversation memory and commanded target state for text and voice."""
import json
import threading

if __package__:
    from .logic import Answer, FINGER_NAMES, apply_target, motion_plan
    from .commands import parse_decision, PARSER_VERSION
else:
    from logic import Answer, FINGER_NAMES, apply_target, motion_plan
    from commands import parse_decision, PARSER_VERSION


class ChatSession:
    def __init__(self, limit=20):
        self.limit = limit
        self.history = []
        self.targets = dict.fromkeys(FINGER_NAMES, None)
        self.last_answer = None
        self.last_parse_decision = None
        self._lock = threading.RLock()

    def plan(self, client, text, model):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Enter a question between 1 and 2000 characters.")
        with self._lock:
            self.last_parse_decision = None
            if text.strip().lower().strip(".!? ") in ("again", "do it again", "repeat that", "repeat", "repeat last gesture"):
                self.last_parse_decision = {"version": PARSER_VERSION,
                    "status": "matched" if self.last_answer else "clarification",
                    "reason": "Repeat previous validated plan" if self.last_answer else "No previous plan"}
                if self.last_answer is None:
                    return Answer("unknown", text="Give me a gesture to repeat first.")
                return self.last_answer
            messages = [dict(message) for message in self.history[-self.limit:]]
            targets = self.targets.copy()
            previous = self.last_answer
        decision = parse_decision(text, targets, previous)
        self.last_parse_decision = {"version": decision.version, "status": decision.status, "reason": decision.reason}
        if decision.status == "rejected":
            raise ValueError(decision.reason)
        if decision.answer is not None:
            return decision.answer
        context = "Current symbolic commanded finger opening percentages (null = unknown, not measured): " + json.dumps(targets)
        messages.append({"role": "user", "content": text.strip()})
        return client.chat(messages, model, context=context)

    def record_motion(self, motion):
        with self._lock:
            self.targets = apply_target(self.targets, motion)

    def commit(self, text, answer):
        with self._lock:
            steps = answer.steps if answer.action == "sequence" else (answer,)
            structured = {"answer": answer.text, "repeat": answer.repeat, "steps": [
                {"action": step.action, "number": step.number, "expression": None,
                 "fingers": step.fingers, "speed": step.speed, "hold_seconds": step.hold_seconds}
                for step in steps]}
            self.history.extend([{"role": "user", "content": text},
                                 {"role": "assistant", "content": json.dumps(structured, ensure_ascii=False)}])
            self.history = self.history[-self.limit:]
            if motion_plan(answer):
                self.last_answer = answer

    def clear(self):
        with self._lock:
            self.history.clear()
            self.last_answer = None
