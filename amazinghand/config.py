from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


SERIAL_BAUDRATE = 1_000_000

FINGER_ORDER = ("index", "middle", "ring", "thumb")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "left_config.json"


@dataclass
class SerialConfig:
    port: str = "COM10"
    baudrate: int = SERIAL_BAUDRATE
    timeout: float = 0.5


@dataclass
class DemoConfig:
    max_speed: int = 7
    close_speed: int = 3


@dataclass
class FingerConfig:
    servos: list[int]
    middle: list[float]
    sim_signs: list[float] = field(default_factory=lambda: [1.0, 1.0])


@dataclass
class HandConfig:
    name: str = "AmazingHand"
    serial: SerialConfig = field(default_factory=SerialConfig)
    side: str = "left"
    demo: DemoConfig = field(default_factory=DemoConfig)
    fingers: dict[str, FingerConfig] = field(
        default_factory=lambda: {
            "index": FingerConfig([1, 2], [7, 5]),
            "middle": FingerConfig([3, 4], [0, 7]),
            "ring": FingerConfig([5, 6], [5, 7]),
            "thumb": FingerConfig([7, 8], [0, 7]),
        }
    )
    poses: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HandConfig":
        serial_data = data.get("serial", {})
        demo_data = data.get("demo", {})
        fingers_data = data.get("fingers", {})
        poses_data = data.get("poses", {})

        fingers: dict[str, FingerConfig] = {}
        defaults = cls().fingers
        for name in FINGER_ORDER:
            source = fingers_data.get(name, {})
            fallback = defaults[name]
            servos = source.get("servos", fallback.servos)
            middle = source.get("middle", fallback.middle)
            sim_signs = source.get("sim_signs", fallback.sim_signs)
            fingers[name] = FingerConfig(
                servos=[int(servos[0]), int(servos[1])],
                middle=[float(middle[0]), float(middle[1])],
                sim_signs=[float(sim_signs[0]), float(sim_signs[1])],
            )

        poses: dict[str, dict[str, list[float]]] = {}
        for pose_name, pose_data in poses_data.items():
            if not isinstance(pose_data, dict):
                continue
            pose: dict[str, list[float]] = {}
            for finger_name in FINGER_ORDER:
                if finger_name not in pose_data:
                    continue
                values = pose_data[finger_name]
                pose[finger_name] = [float(values[0]), float(values[1])]
            if pose:
                poses[str(pose_name)] = pose

        return cls(
            name=str(data.get("name", "AmazingHand")),
            serial=SerialConfig(
                port=str(serial_data.get("port", "COM10")),
                baudrate=SERIAL_BAUDRATE,
                timeout=float(serial_data.get("timeout", 0.5)),
            ),
            side=str(data.get("side", "left")).lower(),
            demo=DemoConfig(
                max_speed=int(demo_data.get("max_speed", 7)),
                close_speed=int(demo_data.get("close_speed", 3)),
            ),
            fingers=fingers,
            poses=poses,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "serial": {
                "port": self.serial.port,
                "baudrate": self.serial.baudrate,
                "timeout": self.serial.timeout,
            },
            "side": self.side,
            "demo": {
                "max_speed": self.demo.max_speed,
                "close_speed": self.demo.close_speed,
            },
            "fingers": {
                name: {
                    "servos": list(self.fingers[name].servos),
                    "middle": list(self.fingers[name].middle),
                    "sim_signs": list(self.fingers[name].sim_signs),
                }
                for name in FINGER_ORDER
            },
            "poses": {
                name: {finger: list(angles) for finger, angles in pose.items()}
                for name, pose in sorted(self.poses.items())
            },
        }


def load_config(path: str | Path | None = None) -> HandConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as stream:
        return HandConfig.from_dict(json.load(stream))


def save_config(config: HandConfig, path: str | Path) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as stream:
        json.dump(config.to_dict(), stream, indent=2)
        stream.write("\n")
