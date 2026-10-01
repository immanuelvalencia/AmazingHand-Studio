from __future__ import annotations

import argparse
from pathlib import Path
import time

from .config import DEFAULT_CONFIG_PATH, FINGER_ORDER, load_config, save_config
from .controller import AmazingHandController
from .gestures import AmazingHandGestures
from .simulation import MuJoCoBridge


GESTURES = {
    "open": "open_hand",
    "close": "close_hand",
    "progressive-open": "open_hand_progressive",
    "spread": "spread_hand",
    "clench": "clench_hand",
    "point": "index_pointing",
    "no-no": "no_no",
    "perfect": "perfect",
    "victory": "victory",
    "scissors": "scissors",
    "pinched": "pinched",
    "middle-finger": "middle_finger",
    "sequence": "run_demo_sequence",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AmazingHand Studio: calibration, control, and demo tools")
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to a JSON config")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to a JSON config")
    subparsers = parser.add_subparsers(dest="command")

    ui = subparsers.add_parser("ui", parents=[config_parent], help="Open the desktop UI")
    ui.set_defaults(func=run_ui)

    demo = subparsers.add_parser("demo", parents=[config_parent], help="Run a demo gesture")
    demo.add_argument("gesture", nargs="?", default="sequence", help="Built-in gesture or saved pose name")
    demo.add_argument("--loop", action="store_true", help="Repeat until Ctrl+C")
    demo.set_defaults(func=run_demo)

    middle = subparsers.add_parser("middle", parents=[config_parent], help="Move one finger to its calibrated middle")
    middle.add_argument("finger", choices=FINGER_ORDER)
    middle.set_defaults(func=run_middle)

    test = subparsers.add_parser("finger-test", parents=[config_parent], help="Cycle one finger closed and open")
    test.add_argument("finger", choices=FINGER_ORDER)
    test.set_defaults(func=run_finger_test)

    init = subparsers.add_parser("init-config", help="Write a default JSON config")
    init.add_argument("output", nargs="?", default="configs/my_hand_config.json")
    init.set_defaults(func=run_init_config)

    sim = subparsers.add_parser("sim", parents=[config_parent], help="Open the MuJoCo viewer")
    sim.add_argument("--mjcf", default=None, help="Path to the MuJoCo scene XML (default: demo model for the configured side)")
    sim.set_defaults(func=run_sim)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args.func = run_ui
    args.func(args)
    return 0


def run_ui(args: argparse.Namespace) -> None:
    from .qt_app import run_app
    run_app(config_path=args.config)


def run_demo(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    gestures = AmazingHandGestures(AmazingHandController(config))
    if args.gesture in GESTURES:
        action = getattr(gestures, GESTURES[args.gesture])
    elif args.gesture in config.poses:
        action = lambda: gestures.move_pose(config.poses[args.gesture], config.demo.max_speed)
    else:
        built_ins = ", ".join(sorted(GESTURES))
        saved = ", ".join(sorted(config.poses)) or "none"
        raise SystemExit(f"Unknown demo '{args.gesture}'. Built-ins: {built_ins}. Saved poses: {saved}.")
    try:
        while True:
            gestures.torque_on()
            action()
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        gestures.torque_free()


def run_middle(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    gestures = AmazingHandGestures(AmazingHandController(config))
    gestures.torque_on()
    gestures.move_middle(args.finger)


def run_finger_test(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    gestures = AmazingHandGestures(AmazingHandController(config))
    gestures.torque_on()
    try:
        while True:
            gestures.close_finger(args.finger)
            gestures._sleep_interruptible(3.0, lambda: False)
            gestures.open_finger(args.finger)
            gestures._sleep_interruptible(1.0, lambda: False)
    except KeyboardInterrupt:
        gestures.torque_free()


def run_init_config(args: argparse.Namespace) -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    save_config(config, Path(args.output))
    print(f"Wrote {args.output}")


def run_sim(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    bridge = MuJoCoBridge(config, args.mjcf)
    bridge.launch_viewer()
    print("MuJoCo viewer launched. Close the viewer window to exit.")
    try:
        while bridge.viewer_running:
            time.sleep(0.02)
    except KeyboardInterrupt:
        bridge.close_viewer()


if __name__ == "__main__":
    raise SystemExit(main())
