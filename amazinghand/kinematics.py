from __future__ import annotations

import numpy as np

from .config import FINGER_ORDER, HandConfig
from .simulation import MuJoCoBridge, scene_path_for_side


class AmazingHandKinematics:
    """Joint-and-line view of the same constrained MJCF used by the demo."""

    def __init__(self, config: HandConfig):
        self.config = config
        self.scene_path = scene_path_for_side(config.side)
        self._side = config.side
        self._bridge = MuJoCoBridge(config, self.scene_path)
        self._load_error: str | None = None

    @property
    def model(self):
        return self._bridge.model

    @property
    def data(self):
        return self._bridge.data

    @property
    def mujoco(self):
        return self._bridge.mujoco

    def update_config(self, config: HandConfig) -> None:
        if config.side != self._side:
            self._side = config.side
            self.scene_path = scene_path_for_side(config.side)
            self._bridge = MuJoCoBridge(config, self.scene_path)
            self._load_error = None
        self.config = config
        self._bridge.refresh_mapping(config)

    def points_for_pose(
        self,
        visual_angles: dict[str, list[float]],
    ) -> tuple[dict[str, np.ndarray], list[tuple[str, str, str, int]], dict[str, str]]:
        if self._load_error is not None:
            raise RuntimeError(self._load_error)
        try:
            self._bridge.load()
        except Exception as exc:
            self._load_error = f"Hand preview unavailable: {exc}"
            raise RuntimeError(self._load_error) from exc

        servo_positions = {}
        for finger_name in FINGER_ORDER:
            finger = self.config.fingers[finger_name]
            angles = visual_angles.get(finger_name, [0.0, 0.0])
            for index, servo in enumerate(finger.servos):
                servo_positions[servo] = finger.middle[index] + float(angles[index])
        self._bridge.set_servo_positions_degrees(servo_positions)

        raw_points: dict[str, np.ndarray] = {
            "wrist": np.array([0.0, -0.075, 0.0]),
            "palm_center": np.array([0.0, 0.0, 0.080]),
            "palm_left": np.array([0.0, -0.043, 0.050]),
            "palm_right": np.array([0.0, 0.046, 0.050]),
            "palm_top_left": np.array([0.0, -0.035, 0.103]),
            "palm_top_right": np.array([0.0, 0.043, 0.085]),
        }
        if self._side == "right":
            raw_points = {name: point * [-1.0, -1.0, 1.0] for name, point in raw_points.items()}
        bones: list[tuple[str, str, str, int]] = [
            ("wrist", "palm_center", "#475569", 7),
            ("palm_left", "palm_top_left", "#64748b", 5),
            ("palm_right", "palm_top_right", "#64748b", 5),
            ("palm_left", "palm_right", "#64748b", 5),
            ("palm_top_left", "palm_top_right", "#64748b", 5),
            ("palm_center", "palm_top_left", "#94a3b8", 3),
            ("palm_center", "palm_top_right", "#94a3b8", 3),
        ]
        labels: dict[str, str] = {}
        for finger_index, finger_name in enumerate(FINGER_ORDER, start=1):
            names = [
                f"finger{finger_index}_base",
                f"finger{finger_index}_proximal",
                f"finger{finger_index}_distal",
                f"finger{finger_index}_tip",
            ]
            motor_1 = self.data.joint(f"finger{finger_index}_motor1").xanchor
            motor_2 = self.data.joint(f"finger{finger_index}_motor2").xanchor
            raw_finger_points = [
                (motor_1 + motor_2) * 0.5,
                self.data.joint(f"passive2 ({finger_index})").xanchor,
                self.data.joint(f"passive4 ({finger_index})").xanchor,
                self.data.site(f"tip{finger_index}").xpos,
            ]
            for name, point in zip(names, raw_finger_points):
                raw_points[name] = np.array(point, dtype=float)
            for start_name, end_name in zip(names, names[1:]):
                bones.append((start_name, end_name, "#2563eb", 7))
            bones.append(("palm_center", names[0], "#94a3b8", 3))
            labels[finger_name[0].upper()] = names[-1]

        origin = raw_points["palm_center"]
        points = {name: _to_preview_coordinates(point - origin) for name, point in raw_points.items()}
        return points, bones, labels


def _to_preview_coordinates(point: np.ndarray) -> np.ndarray:
    return np.array([point[1], point[2], point[0]], dtype=float)
