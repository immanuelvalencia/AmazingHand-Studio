from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Iterable

import numpy as np

from .config import FINGER_ORDER, HandConfig
from .linkage import ConstrainedHandKinematics


BUNDLED_SIM_ROOT = Path(__file__).resolve().parents[1] / "assets" / "mujoco"
FINGER_MODEL_INDEX = {
    "index": 1,
    "middle": 2,
    "ring": 3,
    "thumb": 4,
}
TIP_POSITION_OFFSETS = {
    "right": {
        1: (-0.025, 0.022, 0.098),
        2: (-0.025, -0.009, 0.092),
        3: (-0.025, -0.040, 0.082),
        4: (0.024, 0.019, 0.017),
    },
    "left": {
        1: (0.025, -0.022, 0.098),
        2: (0.025, 0.009, 0.092),
        3: (0.025, 0.040, 0.082),
        4: (0.024, -0.019, 0.017),
    },
}
TIP_POSITION_SCALE = 1.5


def choose_qp_solver(preferred: str = "quadprog") -> str:
    try:
        import qpsolvers
    except ImportError as exc:
        raise RuntimeError(
            "Mink IK needs qpsolvers plus one solver backend. Install `qpsolvers[daqp]` "
            "or `qpsolvers[quadprog]` in the Python environment running this app."
        ) from exc

    available = getattr(qpsolvers, "available_solvers", [])
    solvers = list(available() if callable(available) else available)
    if preferred and preferred in solvers:
        return preferred
    for candidate in ("daqp", "quadprog", "osqp", "clarabel", "scs"):
        if candidate in solvers:
            return candidate
    if solvers:
        return str(solvers[0])
    raise RuntimeError(
        "Mink IK found qpsolvers, but no solver backend is installed. Install `qpsolvers[daqp]` "
        "or `qpsolvers[quadprog]` in this conda environment."
    )


def scene_path_for_side(side: str) -> Path:
    side = side.lower()
    if side not in ("left", "right"):
        raise ValueError(f"Unknown hand side: {side!r}")
    side_name = "AH_Left" if side == "left" else "AH_Right"
    scene = BUNDLED_SIM_ROOT / side_name / "mjcf" / "scene.xml"
    if not scene.is_file():
        raise FileNotFoundError(f"Bundled hand scene is missing: {scene}")
    return scene


def scene_side(scene_path: str | Path) -> str:
    path = Path(scene_path)
    if _scene_looks_like_side(path, "left"):
        return "left"
    if _scene_looks_like_side(path, "right"):
        return "right"
    return "unknown"


def _scene_looks_like_side(scene_path: Path, side: str) -> bool:
    robot_path = scene_path.with_name("robot.xml")
    if not robot_path.exists():
        return False
    try:
        text = robot_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    marker = "l_wrist_interface" if side.lower() == "left" else "r_wrist_interface"
    return marker in text


def _viewer_process_main(
    mjcf_path: str,
    config_dict: dict,
    command_queue: mp.Queue,
    status_queue: mp.Queue,
) -> None:
    bridge = MuJoCoBridge(HandConfig.from_dict(config_dict), mjcf_path)
    try:
        bridge.load()
        import mujoco.viewer

        with mujoco.viewer.launch_passive(bridge.model, bridge.data) as viewer:
            bridge.viewer = viewer
            viewer.sync()
            status_queue.put(("ready", str(bridge.mjcf_path)))
            while viewer.is_running():
                if not _apply_viewer_commands(bridge, command_queue):
                    break

                viewer.sync()
                time.sleep(1.0 / 60.0)
    except Exception as exc:
        status_queue.put(("error", repr(exc)))


def _apply_viewer_commands(bridge: MuJoCoBridge, command_queue) -> bool:
    # A pose can arrive as eight servo commands. Apply the latest target for
    # every motor together, instead of displaying just one servo per frame and
    # accumulating seconds of lag behind the real hand.
    targets = {}
    for _ in range(512):
        try:
            command, payload = command_queue.get_nowait()
        except queue.Empty:
            break
        if command == "close":
            return False
        if command == "servo_positions":
            for servo_id, degrees in payload.items():
                motor = bridge._servo_to_motor.get(int(servo_id))
                if motor is not None:
                    targets[motor] = bridge.servo_to_model_degrees(int(servo_id), float(degrees))
        elif command == "motor_positions":
            targets.update({str(k): float(v) for k, v in payload.items()})
        elif command == "config":
            bridge.set_config(HandConfig.from_dict(payload))
    if targets:
        bridge.set_motor_positions_degrees(targets)
    return True


class MuJoCoBridge:
    def __init__(self, config: HandConfig, mjcf_path: str | Path | None = None):
        self.config = config
        self.mjcf_path = Path(mjcf_path) if mjcf_path else scene_path_for_side(config.side)
        self.model = None
        self.data = None
        self.viewer = None
        self.mujoco = None
        self._lock = threading.RLock()
        self._actuator_ids: dict[str, int] = {}
        self._joint_qpos_addresses: dict[str, int] = {}
        self._kinematics: ConstrainedHandKinematics | None = None
        self._motor_targets: dict[str, float] = {}
        self._servo_to_motor: dict[int, str] = {}
        self._servo_middle_offsets_degrees: dict[int, float] = {}
        self._servo_sim_signs: dict[int, float] = {}
        self._viewer_process: mp.Process | None = None
        self._viewer_commands: mp.Queue | None = None
        self._viewer_status: mp.Queue | None = None
        self.refresh_mapping(config)

    @property
    def loaded(self) -> bool:
        return self.model is not None and self.data is not None and self._kinematics is not None

    @property
    def viewer_running(self) -> bool:
        if self._viewer_process is not None:
            return self._viewer_process.is_alive()
        return bool(self.viewer is not None and self.viewer.is_running())

    def refresh_mapping(self, config: HandConfig) -> None:
        self.config = config
        mapping: dict[int, str] = {}
        offsets: dict[int, float] = {}
        signs: dict[int, float] = {}
        for finger_name in FINGER_ORDER:
            finger_number = FINGER_MODEL_INDEX[finger_name]
            finger = config.fingers[finger_name]
            servo_1, servo_2 = finger.servos
            mapping[int(servo_1)] = f"finger{finger_number}_motor1"
            mapping[int(servo_2)] = f"finger{finger_number}_motor2"
            offsets[int(servo_1)] = float(finger.middle[0])
            offsets[int(servo_2)] = float(finger.middle[1])
            signs[int(servo_1)] = float(finger.sim_signs[0])
            signs[int(servo_2)] = float(finger.sim_signs[1])
        self._servo_to_motor = mapping
        self._servo_middle_offsets_degrees = offsets
        self._servo_sim_signs = signs

    def load(self) -> None:
        if self.loaded:
            return
        try:
            import mujoco
        except ImportError as exc:
            raise RuntimeError("mujoco is not installed. Install it with `pip install mujoco`.") from exc

        if not self.mjcf_path.exists():
            raise FileNotFoundError(f"MuJoCo scene not found: {self.mjcf_path}")

        self.mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(str(self.mjcf_path))
        self.data = mujoco.MjData(self.model)
        self._cache_model_ids()
        zero_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_KEY, "zero")
        if zero_id >= 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, zero_id)
        kinematics = ConstrainedHandKinematics(self.model, self.data)
        kinematics.set_motor_positions({name: 0.0 for name in self._actuator_ids})
        self._kinematics = kinematics
        self._motor_targets = {name: 0.0 for name in self._actuator_ids}

    def launch_viewer(self) -> None:
        if self.viewer_running:
            return
        command_queue: mp.Queue = mp.Queue()
        status_queue: mp.Queue = mp.Queue()
        process = mp.Process(
            target=_viewer_process_main,
            args=(str(self.mjcf_path), self.config.to_dict(), command_queue, status_queue),
            daemon=True,
        )
        process.start()
        self._viewer_process = process
        self._viewer_commands = command_queue
        self._viewer_status = status_queue
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not process.is_alive():
                break
            try:
                status, detail = status_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if status == "ready":
                return
            if status == "error":
                raise RuntimeError(f"MuJoCo viewer failed: {detail}")
        if not process.is_alive():
            raise RuntimeError("MuJoCo viewer closed before it was ready.")

    def close_viewer(self) -> None:
        if self._viewer_commands is not None:
            try:
                self._viewer_commands.put(("close", None))
            except Exception:
                pass
        if self._viewer_process is not None:
            self._viewer_process.join(timeout=1.0)
            if self._viewer_process.is_alive():
                self._viewer_process.terminate()
        self._viewer_process = None
        self._viewer_commands = None
        self._viewer_status = None
        if self.viewer is not None:
            self.viewer.close()
        self.viewer = None

    def set_config(self, config: HandConfig) -> None:
        with self._lock:
            self.refresh_mapping(config)
            self._send_to_viewer("config", config.to_dict())

    def set_servo_position_degrees(self, servo_id: int, degrees: float) -> None:
        motor_name = self._servo_to_motor.get(int(servo_id))
        if motor_name is None:
            return
        model_degrees = self.servo_to_model_degrees(servo_id, degrees)
        self.set_motor_position_degrees(motor_name, model_degrees)

    def set_servo_positions_degrees(self, positions_by_servo: dict[int, float]) -> None:
        self._send_to_viewer("servo_positions", {int(k): float(v) for k, v in positions_by_servo.items()})
        if not self.loaded and self.viewer_running:
            return
        self.load()
        with self._lock:
            for servo_id, degrees in positions_by_servo.items():
                motor_name = self._servo_to_motor.get(int(servo_id))
                if motor_name is not None:
                    model_degrees = self.servo_to_model_degrees(servo_id, degrees)
                    self._set_motor_position_radians(motor_name, np.deg2rad(model_degrees))
            self._forward_and_sync()

    def set_motor_positions_degrees(self, positions_by_motor: dict[str, float]) -> None:
        self._send_to_viewer("motor_positions", {str(k): float(v) for k, v in positions_by_motor.items()})
        if not self.loaded and self.viewer_running:
            return
        self.load()
        with self._lock:
            for motor_name, degrees in positions_by_motor.items():
                self._set_motor_position_radians(motor_name, np.deg2rad(float(degrees)))
            self._forward_and_sync()

    def set_finger_position_degrees(self, finger_name: str, degrees_1: float, degrees_2: float) -> None:
        finger = self.config.fingers[finger_name]
        self.set_servo_positions_degrees(
            {
                finger.servos[0]: degrees_1,
                finger.servos[1]: degrees_2,
            }
        )

    def set_motor_position_degrees(self, motor_name: str, degrees: float) -> None:
        self._send_to_viewer("motor_positions", {motor_name: float(degrees)})
        if not self.loaded and self.viewer_running:
            return
        self.load()
        with self._lock:
            self._set_motor_position_radians(motor_name, np.deg2rad(float(degrees)))
            self._forward_and_sync()

    def servo_to_model_degrees(self, servo_id: int, servo_degrees: float) -> float:
        centered_degrees = float(servo_degrees) - self._servo_middle_offsets_degrees.get(int(servo_id), 0.0)
        return centered_degrees * self._servo_sim_signs.get(int(servo_id), 1.0)

    def model_to_servo_degrees(self, servo_id: int, model_degrees: float) -> float:
        signed_degrees = float(model_degrees) * self._servo_sim_signs.get(int(servo_id), 1.0)
        return signed_degrees + self._servo_middle_offsets_degrees.get(int(servo_id), 0.0)

    def read_hardware_once(self, read_positions: Callable[[Iterable[int]], list[float]]) -> dict[int, float]:
        servo_ids = list(self._servo_to_motor)
        positions = read_positions(servo_ids)
        result = {servo_id: positions[index] for index, servo_id in enumerate(servo_ids)}
        self.set_servo_positions_degrees(result)
        return result

    def _cache_model_ids(self) -> None:
        self._actuator_ids.clear()
        self._joint_qpos_addresses.clear()
        for motor_name in self._servo_to_motor.values():
            actuator_id = self.mujoco.mj_name2id(self.model, self.mujoco.mjtObj.mjOBJ_ACTUATOR, motor_name)
            joint_id = self.mujoco.mj_name2id(self.model, self.mujoco.mjtObj.mjOBJ_JOINT, motor_name)
            if actuator_id < 0:
                raise RuntimeError(f"MuJoCo actuator not found: {motor_name}")
            if joint_id < 0:
                raise RuntimeError(f"MuJoCo joint not found: {motor_name}")
            self._actuator_ids[motor_name] = int(actuator_id)
            self._joint_qpos_addresses[motor_name] = int(self.model.jnt_qposadr[joint_id])

    def _set_motor_position_radians(self, motor_name: str, radians: float) -> None:
        if motor_name not in self._actuator_ids:
            raise RuntimeError(f"MuJoCo motor is not mapped: {motor_name}")
        if not np.isfinite(radians):
            raise ValueError(f"Motor angle must be finite: {motor_name}")
        self._motor_targets[motor_name] = float(radians)

    def _forward_and_sync(self) -> None:
        # Solve passive joints before rendering: mj_forward alone leaves the
        # parallel linkages disconnected. No dynamics stepping is needed.
        try:
            self._kinematics.set_motor_positions(self._motor_targets)
        finally:
            self._motor_targets = {
                name: float(self.data.qpos[address])
                for name, address in self._joint_qpos_addresses.items()
            }
        if self.viewer is not None and self.viewer_running:
            with self.viewer.lock():
                self.viewer.sync()

    def _send_to_viewer(self, command: str, payload: object) -> None:
        if self._viewer_commands is None or not self.viewer_running:
            return
        try:
            self._viewer_commands.put_nowait((command, payload))
        except Exception:
            pass


class MinkIKBridge:
    def __init__(self, config: HandConfig, mjcf_path: str | Path | None = None, mode: str = "quat"):
        self.config = config
        self.mjcf_path = Path(mjcf_path) if mjcf_path else scene_path_for_side(config.side)
        self.mode = mode
        self.mujoco = None
        self.mink = None
        self.model = None
        self.data = None
        self.viewer = None
        self.configuration = None
        self.posture_task = None
        self.tasks = []
        self.tip_tasks = []
        self.solver = "quadprog"
        self._lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return self.model is not None and self.data is not None

    @property
    def viewer_running(self) -> bool:
        return bool(self.viewer is not None and self.viewer.is_running())

    def load(self) -> None:
        if self.loaded:
            return
        try:
            import mujoco
            import mink
        except ImportError as exc:
            raise RuntimeError(
                "MuJoCo IK preview needs `mujoco`, `mink`, and `qpsolvers` with a solver backend. "
                "Install dependencies with `python -m pip install -r requirements.txt`."
            ) from exc
        if not self.mjcf_path.exists():
            raise FileNotFoundError(f"MuJoCo scene not found: {self.mjcf_path}")

        self.solver = choose_qp_solver(self.solver)
        self.mujoco = mujoco
        self.mink = mink
        self.model = mujoco.MjModel.from_xml_path(str(self.mjcf_path))
        self.configuration = mink.Configuration(self.model)
        self.posture_task = mink.PostureTask(self.model, cost=1e-2)
        self.tip_tasks = [self._make_tip_task(index) for index in range(1, 5)]
        eq_task = mink.EqualityConstraintTask(self.model, cost=1000.0)
        self.tasks = [eq_task, self.posture_task, *self.tip_tasks]
        self.model = self.configuration.model
        self.data = self.configuration.data
        self.configuration.update_from_keyframe("zero")
        self.posture_task.set_target_from_configuration(self.configuration)
        self._move_mocaps_to_tips()
        self.mujoco.mj_forward(self.model, self.data)

    def launch_viewer(self) -> None:
        self.load()
        if self.viewer_running:
            self.viewer.sync()
            return
        try:
            import mujoco.viewer
        except ImportError as exc:
            raise RuntimeError("mujoco.viewer is not available in this MuJoCo install.") from exc
        self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        self.viewer.sync()

    def close_viewer(self) -> None:
        if self.viewer is not None:
            self.viewer.close()
        self.viewer = None

    def apply_reference_quaternion_sample(self, elapsed_seconds: float, dt: float = 0.001) -> dict[str, float]:
        self.load()
        quats = reference_tip_quaternions(self.config.side, elapsed_seconds)
        self.set_tip_quaternions(quats, dt=dt)
        return self.motor_positions_degrees()

    def set_tip_quaternions(self, quaternions: dict[str, Iterable[float]], dt: float = 0.001) -> None:
        self.load()
        with self._lock:
            for tip_index in range(1, 5):
                key = f"{self._prefix}_tip{tip_index}"
                if key in quaternions:
                    self.data.mocap_quat[tip_index - 1] = list(quaternions[key])
            self.solve_once(dt=dt)

    def set_tip_positions(self, positions: dict[str, Iterable[float]], dt: float = 0.001) -> None:
        self.load()
        offsets = TIP_POSITION_OFFSETS.get(self.config.side, TIP_POSITION_OFFSETS["right"])
        with self._lock:
            for tip_index in range(1, 5):
                key = f"{self._prefix}_tip{tip_index}"
                if key not in positions:
                    continue
                x, y, z = [float(value) for value in positions[key]]
                ox, oy, oz = offsets[tip_index]
                self.data.mocap_pos[tip_index - 1] = [
                    x * TIP_POSITION_SCALE + ox,
                    y * TIP_POSITION_SCALE + oy,
                    z * TIP_POSITION_SCALE + oz,
                ]
            self.solve_once(dt=dt)

    def solve_once(self, dt: float = 0.001) -> None:
        for tip_index, task in enumerate(self.tip_tasks, start=1):
            task.set_target(
                self.mink.SE3.from_mocap_name(self.model, self.data, f"finger{tip_index}_target")
            )
        velocity = self.mink.solve_ik(self.configuration, self.tasks, dt, self.solver, 1e-5)
        self.configuration.integrate_inplace(velocity, dt)
        self.mujoco.mj_forward(self.model, self.data)
        if self.viewer_running:
            self.viewer.sync()

    def motor_positions_degrees(self) -> dict[str, float]:
        self.load()
        result = {}
        for tip_index in range(1, 5):
            for motor_index in range(1, 3):
                joint_name = f"finger{tip_index}_motor{motor_index}"
                result[joint_name] = float(np.rad2deg(self.data.joint(joint_name).qpos[0]))
        return result

    @property
    def _prefix(self) -> str:
        return "l" if self.config.side == "left" else "r"

    def _make_tip_task(self, tip_index: int):
        orientation_cost = 1.0 if self.mode == "quat" else 0.0
        position_cost = 1.0 if self.mode == "pos" else 0.0
        return self.mink.FrameTask(
            frame_name=f"tip{tip_index}",
            frame_type="site",
            position_cost=position_cost,
            orientation_cost=orientation_cost,
            lm_damping=1.0,
        )

    def _move_mocaps_to_tips(self) -> None:
        for tip_index in range(1, 5):
            self.mink.move_mocap_to_frame(
                self.model,
                self.data,
                f"finger{tip_index}_target",
                f"tip{tip_index}",
                "site",
            )


def reference_tip_quaternions(side: str, elapsed_seconds: float) -> dict[str, np.ndarray]:
    prefix = "l" if side.lower() == "left" else "r"
    side_sign = -1.0 if prefix == "l" else 1.0
    s1_pitch = np.sin(2.0 * np.pi * elapsed_seconds) * np.radians(10.0) + np.radians(10.0)
    s1_roll = np.cos(2.0 * np.pi * elapsed_seconds) * np.radians(10.0) * side_sign
    s2_pitch = np.sin(2.0 * np.pi * elapsed_seconds) * np.radians(140.0 / 2.0) + np.radians(140.0 / 2.0)
    s4_pitch = (
        np.sin(2.0 * np.pi * elapsed_seconds) * np.radians((90.0 + 53.0) / 2.0)
        + np.radians((90.0 - 53.0) / 2.0)
    )

    return {
        f"{prefix}_tip1": _quat_from_euler_xyz(s1_roll, s1_pitch, 0.0),
        f"{prefix}_tip2": _quat_from_euler_xyz(np.radians(10.0) * side_sign, s2_pitch, 0.0),
        f"{prefix}_tip3": _quat_from_euler_xyz(np.radians(20.0) * side_sign, s2_pitch, 0.0),
        f"{prefix}_tip4": _quat_from_euler_xyz(0.0, -s4_pitch, np.radians(20.0) * side_sign),
    }


def _quat_from_euler_xyz(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr = np.cos(roll * 0.5)
    sr = np.sin(roll * 0.5)
    cp = np.cos(pitch * 0.5)
    sp = np.sin(pitch * 0.5)
    cy = np.cos(yaw * 0.5)
    sy = np.sin(yaw * 0.5)
    return np.array(
        [
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        ],
        dtype=float,
    )


class HardwareTracker:
    def __init__(
        self,
        bridge: MuJoCoBridge,
        read_positions: Callable[[Iterable[int]], list[float]],
        interval_seconds: float = 0.1,
        on_update: Callable[[dict[int, float]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.bridge = bridge
        self.read_positions = read_positions
        self.interval_seconds = interval_seconds
        self.on_update = on_update
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                positions = self.bridge.read_hardware_once(self.read_positions)
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(exc)
                self._stop_event.set()
                return
            if self.on_update is not None:
                self.on_update(positions)
            time.sleep(self.interval_seconds)
