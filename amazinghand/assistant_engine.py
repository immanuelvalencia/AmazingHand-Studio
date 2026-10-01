"""Shared chat worker and a hand adapter for the main app's calibrated controller."""
from copy import deepcopy
import json
from pathlib import Path
import threading

from amazinghand.chat.cancellation import Cancelled
from amazinghand.chat.chat_session import ChatSession
from amazinghand.chat.logic import FINGER_NAMES, motion_plan
from .fast_chat import FastOllamaClient as OllamaClient
from amazinghand.chat.settings import load_settings, validate_settings
from .controller import TORQUE_FREE, TORQUE_ON
from .gestures import AmazingHandGestures

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "configs" / "assistant.json"


def assistant_settings(values):
    return validate_settings(values)


def load_assistant_settings():
    if SETTINGS_PATH.exists():
        return assistant_settings(json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig")))
    return assistant_settings(load_settings())


def save_assistant_settings(values):
    temporary = SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(assistant_settings(values), indent=2) + "\n", encoding="utf-8")
    temporary.replace(SETTINGS_PATH)


class CommandController:
    """Mirror exact servo commands; preview never calls the physical controller."""
    def __init__(self, config, cancel, emit, physical=None):
        self.config = deepcopy(config)
        self.cancel, self.emit, self.physical = cancel, emit, physical

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()
        if self.physical is not None and (not self.physical.connected or self.physical.motion_blocked):
            raise RuntimeError("Hand disconnected or stopped. Reconnect before a new hardware request.")

    def set_speed(self, servo, speed):
        self.check()
        if self.physical is not None:
            self.physical.set_speed(servo, min(7, max(1, speed)))

    def set_position_degrees(self, servo, degrees):
        self.check()
        if self.physical is not None:
            self.physical.set_position_degrees(servo, degrees)
            self.check()
        self.emit("servo", (servo, degrees))

    def write_torque(self, servos, state):
        if state != TORQUE_FREE:
            self.check()
        if self.physical is not None and self.physical.connected:
            self.physical.write_torque(servos, state)
        self.emit("torque", state)


class ChatHand:
    def __init__(self, config, cancel, emit, session, physical=None):
        self.controller = CommandController(config, cancel, emit, physical)
        self.gestures = AmazingHandGestures(self.controller)
        self.cancel, self.emit, self.session = cancel, emit, session

    def perform(self, answer):
        plan = motion_plan(answer, default_hold=0.65 if self.controller.physical is None else 1.5)
        if not plan:
            return
        try:
            torque = False
            for motion in plan:
                self.controller.check()
                self.controller.config.demo.max_speed = motion.speed
                self.controller.config.demo.close_speed = motion.speed
                self.emit("motion", motion)
                if motion.action == "relax":
                    self.gestures.torque_free()
                    torque = False
                else:
                    if not torque:
                        self.gestures.torque_on()
                        torque = True
                    if motion.action == "pose":
                        for name, opening in motion.value.items():
                            angle = 90.0 - 1.25 * opening
                            self.gestures.move_finger(name, angle, -angle, motion.speed)
                    elif motion.action in ("count", "yes"):
                        opened = FINGER_NAMES[:motion.value] if motion.action == "count" else ("thumb",)
                        for name in FINGER_NAMES:
                            (self.gestures.open_finger if name in opened else self.gestures.close_finger)(name)
                    else:
                        {"no": self.gestures.no_no, "peace": self.gestures.victory,
                         "open": self.gestures.open_hand, "close": self.gestures.close_hand,
                         "separator": self.gestures.close_hand, "point": self.gestures.index_pointing,
                         "ok": self.gestures.perfect}[motion.action]()
                self.controller.check()
                self.session.record_motion(motion)
                if self.cancel.wait(motion.hold_seconds):
                    raise Cancelled()
        finally:
            self.gestures.torque_free()


class AssistantEngine:
    def __init__(self, events):
        self.events = events
        self.session = ChatSession()
        self.cancel = threading.Event()
        self.thread = None
        self.physical = None
        self.last_text = ""
        self.warm_thread = None
        self.warm_cancel = threading.Event()

    def prewarm(self, settings):
        """Load in the background; typing and local controls stay available."""
        if self.warm_thread is not None and self.warm_thread.is_alive():
            return
        self.warm_cancel.clear()
        def prepare():
            try:
                with OllamaClient(settings["ollama_url"], settings["timeout_seconds"]) as client:
                    client.warm(settings["model"])
            except Exception:
                # The actual request reports actionable errors; preload is optional.
                pass
        self.warm_thread = threading.Thread(target=prepare, daemon=True)
        self.warm_thread.start()

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def emit(self, kind, value):
        self.events.put((kind, value))

    def stop(self):
        self.cancel.set()
        if self.physical is not None:
            self.physical.block_motion()

    def start(self, mode, settings, config, text="", physical=None):
        if self.active:
            raise RuntimeError("Stop the current conversation first.")
        settings = assistant_settings(settings)
        self.cancel = threading.Event()
        self.physical = physical
        self.session.limit = settings["conversation_history_limit"]
        self.thread = threading.Thread(target=self._run, args=(mode, settings, deepcopy(config), text), daemon=True)
        self.thread.start()

    def _status(self, text):
        self.emit("status", text)

    def _reply(self, text, settings, config):
        self._status("Thinking...")
        with OllamaClient(settings["ollama_url"], settings["timeout_seconds"]) as client:
            client.cancel = self.cancel
            client.on_text = lambda partial: self.emit("draft", partial)
            answer = self.session.plan(client, text, settings["model"])
        if self.cancel.is_set():
            raise Cancelled()
        self.last_text = answer.text
        self.emit("answer", answer)
        motion_plan(answer)  # Validate the complete plan before movement.
        self._status("Moving hand" if self.physical is not None else "Animating hand preview")
        ChatHand(config, self.cancel, self.emit, self.session, self.physical).perform(answer)
        if self.cancel.is_set():
            raise Cancelled()
        self.session.commit(text, answer)

    def _run(self, mode, settings, config, text):
        try:
            if mode == "models":
                with OllamaClient(settings["ollama_url"], settings["timeout_seconds"]) as client:
                    names = [name for name in client.models() if "embed" not in name.lower()]
                self.emit("models", names)
                if settings["model"] in names:
                    self.prewarm(settings)
            elif mode == "text":
                self._reply(text, settings, config)
            else:
                raise ValueError("Only text chat and model refresh are supported.")
        except Cancelled:
            pass
        except Exception as exc:
            if not self.cancel.is_set():
                self.emit("error", str(exc))
        finally:
            self.emit("done", self.cancel.is_set())
