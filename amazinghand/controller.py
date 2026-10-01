from __future__ import annotations

import threading
import time
import math
from contextlib import contextmanager
from typing import Callable, Iterable

import numpy as np

from .config import HandConfig, SERIAL_BAUDRATE
from .diagnostics import list_serial_ports


TORQUE_ON = 1
# SCS0009 torque switch: 0 disables drive, 1 enables it.
# Values 2/3 are not reliable release commands on the connected hardware.
TORQUE_OFF = 0
TORQUE_FREE = 0


class AmazingHandController:
    def __init__(self, config: HandConfig):
        self.config = config
        self._driver = None
        self.position_observer: Callable[[int, float], None] | None = None
        self.last_position_observer_error: Exception | None = None
        self.motion_blocked = False
        self.last_torque_state: int | None = None
        self._io_lock = threading.RLock()
        self._control_owner = None
        self._manually_disconnected = False

    def _check_control_owner(self) -> None:
        if self._control_owner is not None and self._control_owner != threading.get_ident():
            raise RuntimeError("Another hardware action owns the servo bus. Stop it first.")

    @contextmanager
    def exclusive_control(self):
        """Reserve an already connected bus; never reconnect during exclusive control."""
        with self._io_lock:
            if self._control_owner is not None:
                raise RuntimeError("The servo bus is already reserved.")
            if not self.connected:
                raise RuntimeError("Connect the hand first.")
            self._control_owner = threading.get_ident()
        try:
            yield
        finally:
            with self._io_lock:
                self._control_owner = None

    def read_servo_feedback(self, servo_id: int) -> dict[str, float]:
        """SCS0009 load is a signed effort count, not a force or current reading.

        rustypot 1.7's read_present_load already decodes endianness and bit-10
        direction. Do not decode it again or use read_raw_present_load here.
        """
        with self._io_lock:
            self._check_control_owner()
            if not self.connected:
                raise RuntimeError("Feedback unavailable: hand disconnected.")
            result = {
                "position": float(np.rad2deg(self._coerce_scalar(self._driver.read_present_position(int(servo_id))))),
                "load": self._coerce_scalar(self._driver.read_present_load(int(servo_id))),
                "temperature": self._coerce_scalar(self._driver.read_present_temperature(int(servo_id))),
            }
            if not all(math.isfinite(v) for v in result.values()):
                raise RuntimeError(f"Servo {servo_id}: non-finite feedback.")
            if abs(result["load"]) > 1023 or abs(result["position"]) > 150 or not 0 <= result["temperature"] <= 100:
                raise RuntimeError(f"Servo {servo_id}: invalid feedback {result}.")
            return result

    @property
    def connected(self) -> bool:
        return self._driver is not None

    def connect(self, *, automatic: bool = False) -> None:
        with self._io_lock:
            self._check_control_owner()
            if automatic and self._manually_disconnected:
                raise RuntimeError("Hand is disconnected. Press Connect first.")
            if self.connected:
                return
            if self._control_owner is not None:
                raise RuntimeError("Hand disconnected during exclusive control; reconnect manually.")
            try:
                from rustypot import Scs0009PyController
            except ImportError as exc:
                raise RuntimeError(
                    "rustypot is not installed. Run `python -m pip install -r requirements.txt`, "
                    "then restart the app."
                ) from exc

            try:
                self._driver = Scs0009PyController(
                    serial_port=self.config.serial.port,
                    baudrate=SERIAL_BAUDRATE,
                    timeout=self.config.serial.timeout,
                )
                self._manually_disconnected = False
            except Exception as exc:
                self._driver = None
                ports = list_serial_ports()
                port_list = ", ".join(ports) if ports else "none detected"
                hint = (" Close any other Studio, experiment app, or serial monitor using this port, then retry."
                        if "access is denied" in str(exc).lower() else " Check the selected port and USB connection.")
                raise RuntimeError(
                    "Could not open the Feetech serial bus. "
                    f"port={self.config.serial.port!r}, baudrate={self.config.serial.baudrate}, "
                    f"timeout={self.config.serial.timeout}. Available ports: {port_list}. "
                    f"Original error: {exc!r}.{hint}"
                ) from exc

    def disconnect(self) -> None:
        with self._io_lock:
            self._check_control_owner()
            self._manually_disconnected = True
            self.motion_blocked = True
            self.last_torque_state = None
            driver = self._driver
            self._driver = None
            close = getattr(driver, "close", None)
            if callable(close):
                close()

    def set_config(self, config: HandConfig) -> None:
        self._check_control_owner()
        reconnect = self.connected and (
            config.serial.port != self.config.serial.port
            or config.serial.baudrate != self.config.serial.baudrate
            or config.serial.timeout != self.config.serial.timeout
        )
        if reconnect:
            self.disconnect()
        self.config = config

    def set_position_observer(self, observer: Callable[[int, float], None] | None) -> None:
        self.position_observer = observer

    def block_motion(self) -> None:
        self.motion_blocked = True

    def resume_motion(self) -> None:
        self._check_control_owner()
        self.motion_blocked = False

    def all_servo_ids(self) -> list[int]:
        ids: list[int] = []
        for finger in self.config.fingers.values():
            ids.extend(finger.servos)
        return ids

    def write_torque(self, servo_ids: Iterable[int] | None, state: int) -> None:
        with self._io_lock:
            if int(state) == TORQUE_ON and self.motion_blocked:
                raise RuntimeError("Motion was stopped; torque enable cancelled.")
            self.connect(automatic=True)
            ids = list(servo_ids if servo_ids is not None else self.all_servo_ids())
            self.last_torque_state = None
            sync_write = getattr(self._driver, "sync_write_torque_enable", None)
            if callable(sync_write) and len(ids) > 1:
                try:
                    sync_write([int(servo_id) for servo_id in ids], [int(state)] * len(ids))
                    if state == TORQUE_FREE:
                        self._verify_torque_released(ids)
                    self.last_torque_state = int(state) if set(ids) == set(self.all_servo_ids()) else None
                    return
                except Exception:
                    pass

            errors: list[str] = []
            for servo_id in ids:
                try:
                    self._driver.write_torque_enable(int(servo_id), int(state))
                except Exception as exc:
                    errors.append(f"{servo_id}: {exc!r}")
                time.sleep(0.002)
            if errors:
                raise RuntimeError(f"Torque write failed for {len(errors)} servo(s): {', '.join(errors)}")
            if state == TORQUE_FREE:
                self._verify_torque_released(ids)
            self.last_torque_state = int(state) if set(ids) == set(self.all_servo_ids()) else None

    def _verify_torque_released(self, ids) -> None:
        read = getattr(self._driver, "read_torque_enable", None)
        if callable(read):
            for i in ids:
                if self._coerce_scalar(read(int(i))) != 0:
                    raise RuntimeError(f"Servo {i}: torque release was not confirmed by readback.")

    def set_speed(self, servo_id: int, speed: int) -> None:
        if self.motion_blocked:
            return
        with self._io_lock:
            if self.motion_blocked:
                return
            self.connect(automatic=True)
            self._driver.write_goal_speed(int(servo_id), int(speed))
            time.sleep(0.0002)

    def set_position_degrees(self, servo_id: int, degrees: float, *, notify: bool = True) -> None:
        if self.motion_blocked:
            return
        if notify and self.position_observer is not None:
            try:
                self.position_observer(int(servo_id), float(degrees))
                self.last_position_observer_error = None
            except Exception as exc:
                self.last_position_observer_error = exc
        with self._io_lock:
            if self.motion_blocked:
                return
            self.connect(automatic=True)
            self._driver.write_goal_position(int(servo_id), np.deg2rad(float(degrees)))
            time.sleep(0.0002)

    def read_position_degrees(self, servo_id: int) -> float:
        with self._io_lock:
            self.connect(automatic=True)
            raw_position = self._driver.read_present_position(int(servo_id))
            return float(np.rad2deg(self._coerce_scalar(raw_position)))

    def set_positions_degrees(self, targets: dict[int, float], *, notify: bool = True) -> None:
        """Send a pose in one packet so paired motors receive targets together."""
        if not targets or self.motion_blocked:
            return
        ids = list(targets)
        values = [float(targets[i]) for i in ids]
        if not all(math.isfinite(v) and abs(v) <= 150 for v in values):
            raise ValueError("Pose contains an invalid servo angle.")
        with self._io_lock:
            if self.motion_blocked:
                return
            self.connect(automatic=True)
            sync_write = getattr(self._driver, "sync_write_goal_position", None)
            if callable(sync_write):
                sync_write(ids, [float(np.deg2rad(v)) for v in values])
            else:
                for i, v in zip(ids, values):
                    if self.motion_blocked:
                        return
                    self._driver.write_goal_position(i, float(np.deg2rad(v)))
        if notify and self.position_observer is not None:
            for i, v in zip(ids, values):
                try:
                    self.position_observer(i, v)
                    self.last_position_observer_error = None
                except Exception as exc:
                    self.last_position_observer_error = exc

    def read_positions_degrees(self, servo_ids: Iterable[int]) -> list[float]:
        with self._io_lock:
            self.connect(automatic=True)
            ids = [int(servo_id) for servo_id in servo_ids]
            sync_read = getattr(self._driver, "sync_read_present_position", None)
            if callable(sync_read):
                raw_positions = sync_read(ids)
                return [float(np.rad2deg(value)) for value in self._coerce_values(raw_positions, len(ids))]
            return [self.read_position_degrees(servo_id) for servo_id in ids]

    @staticmethod
    def _coerce_scalar(value) -> float:
        if value is None:
            raise RuntimeError("servo returned no position value")
        if hasattr(value, "item"):
            try:
                return float(value.item())
            except ValueError:
                pass
        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise RuntimeError(f"expected one position value, got {value!r}")
            return AmazingHandController._coerce_scalar(value[0])
        return float(value)

    @staticmethod
    def _coerce_values(values, expected_count: int) -> list[float]:
        if values is None:
            raise RuntimeError("servos returned no position values")
        if hasattr(values, "tolist"):
            values = values.tolist()
        if not isinstance(values, (list, tuple)):
            if expected_count == 1:
                return [AmazingHandController._coerce_scalar(values)]
            raise RuntimeError(f"expected {expected_count} position values, got {values!r}")
        if len(values) != expected_count:
            raise RuntimeError(f"expected {expected_count} position values, got {values!r}")
        return [AmazingHandController._coerce_scalar(value) for value in values]
