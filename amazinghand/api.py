"""Public Python control interface; no Qt application required."""
from copy import deepcopy
import math
import threading
from .config import load_config, SERIAL_BAUDRATE
from .diagnostics import list_serial_ports
from .controller import AmazingHandController
from .gestures import AmazingHandGestures
from .qt_presets import pose_preset


class ScriptStopped(BaseException):
    """Raised when a Studio script is cancelled."""


class AmazingHand:
    """Control calibrated motor offsets in degrees. Connection is explicit.

    Use ``AmazingHand(config_path="configs/left_config.json")`` outside Studio.
    Studio supplies an existing ``hand`` instance to each script.
    """
    def __init__(self, config_path=None, *, port=None, baudrate=SERIAL_BAUDRATE,
                 timeout=None, side=None, speed=None, config=None,
                 controller=None, preview=None, cancel=None):
        if baudrate != SERIAL_BAUDRATE:
            raise ValueError(f"AmazingHand requires baudrate={SERIAL_BAUDRATE}")
        if controller is not None and (config_path is not None or config is not None or
                                      any(v is not None for v in (port, timeout, side, speed))):
            raise ValueError("Configure the shared Studio controller through Settings")
        if config is not None and config_path is not None:
            raise ValueError("Use config or config_path, not both")
        if controller is None:
            if config_path is not None:
                from pathlib import Path
                if not Path(config_path).is_file():
                    raise FileNotFoundError(config_path)
            settings = deepcopy(config) if config is not None else load_config(config_path)
            if port is not None:
                if not isinstance(port, str) or not port.strip():
                    raise ValueError("port must be a nonempty device name")
                settings.serial.port = port.strip()
            if timeout is not None:
                if not math.isfinite(float(timeout)) or float(timeout) <= 0:
                    raise ValueError("timeout must be positive and finite")
                settings.serial.timeout = float(timeout)
            if side is not None:
                if side not in ("left", "right"):
                    raise ValueError("side must be left or right")
                settings.side = side
            if speed is not None:
                if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 7:
                    raise ValueError("speed must be an integer from 1 to 7")
                settings.demo.max_speed = speed
        self._shared = controller is not None
        self._controller = controller or AmazingHandController(settings)
        self._preview = preview
        self._cancel = cancel or threading.Event()

    @staticmethod
    def available_ports():
        """Return detected serial device names without opening them."""
        return list_serial_ports()

    @property
    def config(self):
        """Return a copy of port, calibration, side, speed, and saved poses."""
        return deepcopy(self._controller.config)

    def release(self):
        """Disable torque on all motors without closing the serial connection."""
        self._check()
        if not self.connected:
            raise RuntimeError("Connect before releasing hardware")
        self._controller.write_torque(None, 0)

    def read_pose(self):
        """Read calibrated motor offsets from hardware, in degrees."""
        self._check()
        if not self.connected:
            raise RuntimeError("Connect before reading hardware")
        result = {}
        for name, finger in self._controller.config.fingers.items():
            self._check()
            values = self._controller.read_positions_degrees(finger.servos)
            result[name] = [v - m for v, m in zip(values, finger.middle)]
        return result

    @property
    def connected(self):
        return self._controller.connected

    @property
    def saved_poses(self):
        return deepcopy(self._controller.config.poses)

    def _check(self):
        if self._cancel.is_set():
            raise ScriptStopped()

    def connect(self):
        self._check()
        if self._shared:
            raise RuntimeError("Use Studio's Connect button before running code.")
        self._controller.connect()
        self._controller.resume_motion()
        return self

    def disconnect(self):
        if self._shared:
            raise RuntimeError("Use Studio's Disconnect button.")
        self._controller.disconnect()

    def move_pose(self, pose):
        """Move only named fingers, each with two offsets from calibrated middle."""
        self._check()
        config = self._controller.config
        validated = {}
        for name, values in pose.items():
            if name not in config.fingers:
                raise ValueError(f"Unknown finger: {name}")
            if len(values) != 2:
                raise ValueError(f"{name} requires two motor offsets")
            angles = [float(v) for v in values]
            if not all(math.isfinite(v) and abs(v + middle) <= 150
                       for v, middle in zip(angles, config.fingers[name].middle)):
                raise ValueError(f"Invalid angles for {name}; absolute servo targets must be within +/-150 degrees")
            validated[name] = angles
        if self.connected:
            if self._controller.motion_blocked:
                raise ScriptStopped()
            AmazingHandGestures(self._controller).move_powered_pose(validated)
        elif self._preview is not None:
            self._preview(validated)
        else:
            raise RuntimeError("Call hand.connect() before moving.")

    def move_finger(self, name, motor1, motor2):
        self.move_pose({name: [motor1, motor2]})

    def preset(self, name):
        """Middle, Open, Close, Spread, Clench, Point, Victory, or Pinched."""
        self.move_pose(pose_preset(name, self._controller.config.side))

    def saved_pose(self, name):
        self.move_pose(self.saved_poses[name])

    def wait(self, seconds):
        """Cancellable delay; does not confirm arrival at a physical target."""
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Delay must be finite and nonnegative")
        if self._cancel.wait(seconds):
            raise ScriptStopped()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.disconnect()
