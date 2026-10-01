from __future__ import annotations

import time
import math
from typing import Callable

from .config import FINGER_ORDER, HandConfig
from .controller import AmazingHandController, TORQUE_FREE, TORQUE_ON


class AmazingHandGestures:
    def __init__(self, controller: AmazingHandController):
        self.controller = controller

    @property
    def config(self) -> HandConfig:
        return self.controller.config

    @property
    def max_speed(self) -> int:
        return self.config.demo.max_speed

    @property
    def close_speed(self) -> int:
        return self.config.demo.close_speed

    def torque_on(self) -> None:
        self.controller.write_torque(None, TORQUE_ON)

    def torque_free(self) -> None:
        self.controller.write_torque(None, TORQUE_FREE)

    def move_finger(self, finger_name: str, angle_1: float, angle_2: float, speed: int) -> None:
        finger = self.config.fingers[finger_name]
        servo_1, servo_2 = finger.servos
        middle_1, middle_2 = finger.middle
        self.controller.set_speed(servo_1, speed)
        self.controller.set_speed(servo_2, speed)
        self.controller.set_position_degrees(servo_1, middle_1 + angle_1)
        self.controller.set_position_degrees(servo_2, middle_2 + angle_2)
        time.sleep(0.005)

    def move_pose(self, pose: dict[str, list[float] | tuple[float, float]], speed: int | None = None) -> None:
        targets = {}
        for finger_name in FINGER_ORDER:
            if finger_name not in pose:
                continue
            angle_1, angle_2 = pose[finger_name]
            finger = self.config.fingers[finger_name]
            for i, middle, angle in zip(finger.servos, finger.middle, (angle_1, angle_2)):
                self.controller.set_speed(i, speed or self.max_speed)
                targets[i] = middle + float(angle)
        self.controller.set_positions_degrees(targets)

    def move_middle(self, finger_name: str, speed: int | None = None) -> None:
        self.move_finger(finger_name, 0, 0, speed or self.max_speed)

    def move_powered_pose(self, pose) -> None:
        """An explicit pose command also restores torque on just its motors."""
        self.move_pose(pose, self.max_speed)
        if not self.controller.motion_blocked:
            ids = [i for name in pose for i in self.config.fingers[name].servos]
            self.controller.write_torque(ids, TORQUE_ON)

    def open_finger(self, finger_name: str, speed: int | None = None) -> None:
        self.move_finger(finger_name, -35, 35, speed or self.max_speed)

    def close_finger(self, finger_name: str, speed: int | None = None) -> None:
        self.move_finger(finger_name, 90, -90, speed or self.close_speed)

    def open_hand(self) -> None:
        for finger_name in FINGER_ORDER:
            self.open_finger(finger_name, self.max_speed)

    def side_to_side(self, fingers=None, should_stop=None) -> None:
        """Two bounded lateral sweeps about the measured pose, preserving curl."""
        names = tuple(FINGER_ORDER if fingers is None else fingers)
        if not names or len(set(names)) != len(names) or any(n not in FINGER_ORDER for n in names):
            raise ValueError("Select at least one finger.")
        if not self.controller.connected:
            raise RuntimeError("Connect the hand before starting side-to-side movement.")
        stop = lambda: self.controller.motion_blocked or bool(should_stop and should_stop())
        ids = [i for n in names for i in self.config.fingers[n].servos]
        if len(set(self.controller.all_servo_ids())) != len(self.controller.all_servo_ids()):
            raise ValueError("Servo IDs must be unique.")
        initial = {}
        amplitudes = {}
        failed = True
        try:
            for name in names:
                if stop():
                    return
                finger = self.config.fingers[name]
                pair = [self.controller.read_position_degrees(i) for i in finger.servos]
                relative = [p - m for p, m in zip(pair, finger.middle)]
                if not all(math.isfinite(v) and abs(v) <= 90 for v in relative):
                    raise ValueError(f"{name}: position outside calibrated movement range.")
                amplitude = min(12, *(90 - abs(v) for v in relative), *(149 - abs(p) for p in pair))
                if amplitude < 1:
                    raise ValueError(f"{name}: no lateral travel available. Open the finger slightly first.")
                initial.update(zip(finger.servos, pair))
                amplitudes[name] = amplitude
            # Seed current goals before torque enable to avoid stale targets.
            for i in ids:
                if stop():
                    return
                self.controller.set_speed(i, min(3, max(1, self.max_speed)))
                self.controller.set_position_degrees(i, initial[i])
            if stop():
                return
            self.controller.write_torque(ids, TORQUE_ON)
            previous = 0.0
            for target in (1, -1, 1, -1, 0):
                steps = int(abs(target - previous) * 10)
                for step in range(1, steps + 1):
                    frame_started = time.monotonic()
                    t = step / steps
                    eased = t * t * (3 - 2 * t)
                    fraction = previous + (target - previous) * eased
                    targets = {}
                    for name in names:
                        for i in self.config.fingers[name].servos:
                            if stop():
                                return
                            targets[i] = initial[i] + amplitudes[name] * fraction
                    self.controller.set_positions_degrees(targets)
                    self._sleep_interruptible(max(0, 0.02 - (time.monotonic() - frame_started)), stop)
                previous = target
            failed = False
        finally:
            if (failed or stop()) and self.controller.connected:
                self.controller.write_torque(ids, TORQUE_FREE)

    def close_hand(self) -> None:
        for finger_name in FINGER_ORDER:
            speed = self.close_speed + 1 if finger_name == "thumb" else self.close_speed
            self.close_finger(finger_name, speed)

    def open_hand_progressive(self) -> None:
        for finger_name in FINGER_ORDER:
            self.open_finger(finger_name, max(1, self.max_speed - 2))
            time.sleep(0.2)

    def spread_hand(self) -> None:
        if self.config.side == "left":
            angles = {
                "index": (-60, 0),
                "middle": (-35, 35),
                "ring": (-4, 90),
                "thumb": (-4, 90),
            }
        else:
            angles = {
                "index": (4, 90),
                "middle": (-32, 32),
                "ring": (-90, -4),
                "thumb": (-90, -4),
            }
        self._move_many(angles, self.max_speed)

    def clench_hand(self) -> None:
        if self.config.side == "left":
            angles = {
                "index": (0, 60),
                "middle": (-35, 35),
                "ring": (-70, 0),
                "thumb": (-90, -4),
            }
        else:
            angles = {
                "index": (-60, 0),
                "middle": (-35, 35),
                "ring": (0, 70),
                "thumb": (-4, 90),
            }
        self._move_many(angles, self.max_speed)

    def index_pointing(self) -> None:
        self.move_finger("index", -40, 40, self.max_speed)
        for finger_name in ("middle", "ring", "thumb"):
            self.close_finger(finger_name, self.max_speed)

    def no_no(self) -> None:
        self.index_pointing()
        for _ in range(3):
            time.sleep(0.2)
            self.move_finger("index", -10, 80, self.max_speed)
            time.sleep(0.2)
            self.move_finger("index", -80, 10, self.max_speed)
        self.open_finger("index", self.max_speed)

    def perfect(self) -> None:
        self.move_finger("index", 50, -50, self.max_speed)
        self.move_finger("middle", 0, 0, self.max_speed)
        self.move_finger("ring", -20, 20, self.max_speed)
        if self.config.side == "left":
            self.move_finger("thumb", -12, -65, self.max_speed)
        else:
            self.move_finger("thumb", 65, 12, self.max_speed)

    def victory(self) -> None:
        if self.config.side == "left":
            self.move_finger("index", -65, 15, self.max_speed)
            self.move_finger("middle", -15, 65, self.max_speed)
        else:
            self.move_finger("index", -15, 65, self.max_speed)
            self.move_finger("middle", -65, 15, self.max_speed)
        self.close_finger("ring", self.max_speed)
        self.close_finger("thumb", self.max_speed)

    def scissors(self) -> None:
        self.victory()
        for _ in range(3):
            time.sleep(0.2)
            if self.config.side == "left":
                self.move_finger("index", -20, 50, self.max_speed)
                self.move_finger("middle", -50, 20, self.max_speed)
            else:
                self.move_finger("index", -50, 20, self.max_speed)
                self.move_finger("middle", -20, 50, self.max_speed)
            time.sleep(0.2)
            self.victory()

    def pinched(self) -> None:
        for finger_name in ("index", "middle", "ring"):
            self.close_finger(finger_name, self.max_speed)
        if self.config.side == "left":
            self.move_finger("thumb", 75, 5, self.max_speed)
        else:
            self.move_finger("thumb", 0, -75, self.max_speed)

    def middle_finger(self) -> None:
        self.close_finger("index", self.max_speed)
        self.open_finger("middle", self.max_speed)
        self.close_finger("ring", self.max_speed)
        if self.config.side == "left":
            self.move_finger("thumb", 75, 0, self.max_speed)
        else:
            self.move_finger("thumb", 0, -75, self.max_speed)

    def run_demo_sequence(self, should_stop: Callable[[], bool] | None = None) -> None:
        stop = should_stop or (lambda: False)
        sequence: list[tuple[Callable[[], None], float]] = [
            (self.open_hand, 0.5),
            (self.close_hand, 3.0),
            (self.open_hand_progressive, 0.5),
            (self.spread_hand, 0.6),
            (self.clench_hand, 0.6),
            (self.open_hand, 0.2),
            (self.index_pointing, 0.4),
            (self.no_no, 0.5),
            (self.open_hand, 0.3),
            (self.perfect, 0.8),
            (self.open_hand, 0.4),
            (self.victory, 1.0),
            (self.scissors, 0.5),
            (self.open_hand, 0.4),
            (self.pinched, 1.0),
            (self.middle_finger, 0.8),
        ]
        for action, delay in sequence:
            if stop():
                break
            action()
            self._sleep_interruptible(delay, stop)

    def _move_many(self, angles: dict[str, tuple[float, float]], speed: int) -> None:
        for finger_name, pair in angles.items():
            self.move_finger(finger_name, pair[0], pair[1], speed)

    @staticmethod
    def _sleep_interruptible(seconds: float, should_stop: Callable[[], bool]) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if should_stop():
                return
            time.sleep(min(0.01, max(0, end - time.monotonic())))
