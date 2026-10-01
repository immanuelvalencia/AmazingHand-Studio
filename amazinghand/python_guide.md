# Python control guide

## Run code in Studio

The **Code** tab provides `hand`, already configured with the current side, motor IDs, calibration, and saved poses. Click **Connect** before running to move hardware. When disconnected, the same commands update the hand preview only. Click **Run code** or press **Ctrl+Enter**. Output and Python errors appear below the editor. Open and Save as work with ordinary `.py` files.

```python
hand.preset("Open")
hand.wait(1)
hand.move_finger("index", -20, 20)
hand.move_pose({"index": [0, 0], "thumb": [-15, 15]})
print(list(hand.saved_poses))
# hand.saved_pose("My saved pose")
```

Offsets are in degrees relative to each motor's calibrated middle, not fingertip or joint angles. Each finger has two motor offsets. Only named fingers move. Available fingers: `index`, `middle`, `ring`, `thumb`. Absolute servo targets must remain within +/-150 degrees. Commands enable torque on the selected motors. Delays are time delays, not feedback-based arrival checks.

## Presets and loops

Presets: `Middle`, `Open`, `Close`, `Spread`, `Clench`, `Point`, `Victory`, `Pinched`. They follow the selected hand side.

```python
for cycle in range(3):
    hand.preset("Open")
    hand.wait(1)
    hand.preset("Close")
    hand.wait(1)
```

**Stop code**, Escape, and Disconnect request cancellation. Use `hand.wait()` instead of `time.sleep()` for responsive delays. Python loops are interrupted at trace events; blocking native calls finish before cancellation can complete. Stop also requests torque release once the worker exits. Scripts are trusted local Python, with normal file and network access, not a sandbox. Do not start background threads or open a second controller inside Studio. Script `print()` and tracebacks are captured; output from imported libraries may still go to the terminal. Other control tabs are disabled while running.

## Use the library outside Studio

From the project root, run a Python script in the AmazingHand Conda environment. `amazinghand` is an importable package; no GUI launch is required. Install the library for use from other projects with `python -m pip install -e .` from the repository root (or add the repository directory to `PYTHONPATH`). The GUI and simulation still use the full dependencies in `requirements.txt`. Supply the full path to your calibrated configuration when running elsewhere. Close Studio's connection first so only one application owns the serial port.

```python
from amazinghand import AmazingHand

with AmazingHand(config_path="configs/left_config.json") as hand:
    hand.preset("Open")
    hand.wait(1)
    hand.move_finger("index", -20, 20)
```

The context manager connects on entry and closes the serial connection on exit. Disconnect does not guarantee torque release. You can also call `hand.connect()` and `hand.disconnect()` explicitly. Baud is fixed at 1,000,000; port, side, and calibration come from the configuration. `hand.connected` reports connection state, and `hand.saved_poses` returns a copy of the saved poses. Do not call connect/disconnect on the provided Studio `hand`; use the app's button.


## Complete standalone hardware setup

Click **Hardware example** in the editor to load a complete script. It runs in Studio with the existing `hand`, or in a terminal with its own connection. In Studio, connection constants in that example are not applied: change Settings instead. Outside Studio those constants configure the connection. Save the example as your own `.py` file and run it with Python.

```powershell
conda activate amazinghand-studio
python -m pip install -e .
python -m amazinghand.hardware_example
# Or run your saved file:
python my_hand_script.py
```

Edit the example's configuration path and port before running. Discover ports without opening a connection:

```python
from amazinghand import AmazingHand
print(AmazingHand.available_ports())
```

A complete connection and cleanup pattern:

```python
from amazinghand import AmazingHand

with AmazingHand(
    config_path="configs/left_config.json",
    port="COM10",
    baudrate=1_000_000,
    timeout=0.5,
    side="left",
    speed=3,
) as hand:
    try:
        hand.preset("Open")
        hand.wait(1.0)
        hand.move_pose({"index": [15, -15], "thumb": [10, -10]})
        hand.wait(1.0)
        print(hand.read_pose())
    finally:
        hand.release()
```

| Setting | Meaning |
| --- | --- |
| `config_path` | JSON calibration profile, including motor IDs, middle offsets, and saved poses. An explicit missing path raises an error. |
| `port` | Serial device, e.g. `COM10` on Windows or `/dev/ttyUSB0` on Linux. Overrides the profile's port. |
| `baudrate` | Must be `1_000_000`. Other baud rates are rejected. |
| `timeout` | Positive serial timeout in seconds; default comes from the profile. |
| `side` | `left` or `right`. Selects side-specific presets; it does not recalibrate motor IDs or offsets. |
| `speed` | Integer controller setting 1–7, not degrees per second. Overrides the profile's maximum movement speed. |
| `config` | Optional `HandConfig` object instead of a JSON path; copied when creating the hand. |

You can configure motor IDs and calibration in Python using measured values for your own hand:

```python
from amazinghand import AmazingHand, load_config

config = load_config("configs/left_config.json")
# Example access: retain or replace these with your measured calibration.
print(config.fingers["index"].servos)
print(config.fingers["index"].middle)
with AmazingHand(config=config, port="COM10", speed=3) as hand:
    try:
        hand.move_finger("index", 0, 0)
        hand.wait(1)
    finally:
        hand.release()
```

## Public control methods

| Python | Behavior |
| --- | --- |
| `AmazingHand.available_ports()` | Lists detected serial ports. |
| `hand.connect()` / `hand.disconnect()` | Explicit standalone connection lifecycle; use Studio's button in the editor. |
| `hand.connected` | Whether the serial connection is open; not a motor-health check. |
| `hand.config` | Copy of active configuration, including port and baud; editing this copy does not change the hand. |
| `hand.move_finger("index", a, b)` | Moves two motors using calibrated degree offsets. |
| `hand.move_pose({"index": [a, b]})` | Moves only specified fingers. |
| `hand.preset("Open")` | Moves to a built-in preset. |
| `hand.saved_poses` | Copy of saved custom poses. |
| `hand.saved_pose("name")` | Executes a saved custom pose. |
| `hand.wait(seconds)` | Cancellable delay. |
| `hand.read_pose()` | Reads each finger's two motor offsets from hardware; requires connection. Reads are sequential, not an atomic snapshot. |
| `hand.release()` | Disables torque on all motors while keeping the port open; requires connection. The next movement command enables selected motors again. |

Hardware exceptions propagate to your script. Use `try/finally` for release and a context manager to close the port even when a command fails. Releasing torque allows the hand to move freely. Absolute angle validation is a controller range check, not collision or mechanical-limit detection.
