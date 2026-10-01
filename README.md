# AmazingHand Studio

**Desktop control and Python scripting for AmazingHand.**

Pose editing · Gesture sequences · LLM control · MuJoCo preview · Python API

[Installation](#installation) · [User guide](#using-studio) · [Python API](#python-api) · [Team](#team) · [Original AmazingHand](https://github.com/pollen-robotics/AmazingHand)

---

## Overview

AmazingHand Studio combines pose editing, gesture sequencing, language-based commands, and Python scripting in a PyQt5 desktop application. It controls the four-finger AmazingHand using configurable motor IDs and calibrated middle positions.

Select a left or right hand profile, create custom poses, build sequences, or write your own hardware-control scripts. A persistent, resizable preview displays commanded positions as a 3D model, a kinematics model, or both.

The hardware and bundled hand models originate from [Pollen Robotics' AmazingHand project](https://github.com/pollen-robotics/AmazingHand). For CAD, assembly, and original control examples, see [Original project and acknowledgments](#original-project-and-acknowledgments).

## Contents

- [Features](#features)
- [Installation](#installation)
- [Using Studio](#using-studio)
- [Python API](#python-api)
- [Repository layout](#repository-layout)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Team](#team)
- [Original project and acknowledgments](#original-project-and-acknowledgments)
- [Copyright and licenses](#copyright-and-licenses)

---

## Features

| Workspace | Capabilities |
| --- | --- |
| **Pose Control** | Individual finger selection, paired motor controls, presets, and named custom poses. |
| **Gestures** | Quick gestures and sequences combining gestures and saved poses, with delays and looping. |
| **LLM Control** | Natural-language interaction through a configured Ollama server and model selector. |
| **Code** | Python syntax highlighting, Run/Stop, file open/save, output console, hardware example, and embedded guide. |
| **Hand preview** | Resizable and floatable dock; orbit and zoom; 3D, kinematics, or separate views of both. |
| **Python library** | Importable `AmazingHand` interface for standalone scripts. |

---

## Installation

### Requirements

| Component | Requirement |
| --- | --- |
| Python | **3.11** for the supplied environment. |
| Windows | Windows 10/11, x64. |
| Ubuntu | Ubuntu 22.04/24.04, x86_64; desktop session for the GUI. |
| Graphics | Working OpenGL drivers for the MuJoCo preview. |
| Hardware | AmazingHand, a compatible serial adapter, and a calibrated configuration. |
| Serial baud rate | **1,000,000**, fixed by the application and library. |
| LLM Control | A running Ollama server with an installed model. |

Clone this repository:

~~~sh
git clone https://github.com/immanuelvalencia/AmazingHand-Studio.git
cd AmazingHand-Studio
~~~

Then run these commands **from the repository root**:

~~~sh
conda env create -f environment.yml
conda activate amazinghand-studio
python -m pip install --no-deps -e .
python -m pip check
python main.py
~~~

For an existing Python 3.11 environment:

~~~sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python main.py
~~~

The editable install lets other projects import `amazinghand`. Reinstall if you move this checkout. PyTorch is not required. Ollama and model weights are installed separately.

**Windows:** install the serial adapter's manufacturer driver if needed, then select its actual COM port in Settings.

### Ubuntu prerequisites

Install the Qt/OpenGL system libraries:

~~~sh
sudo apt update
sudo apt install -y libgl1 libegl1 libglfw3 libdbus-1-3 libfontconfig1 libx11-xcb1 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxcb-shape0
sudo usermod -aG dialout "$USER"
~~~

Log out and back in after changing serial permissions. Use the actual device path, such as `/dev/ttyUSB0`. On Wayland, try `QT_QPA_PLATFORM=xcb python main.py` if Qt reports a platform-plugin error.

**Compatibility status:** dependency resolution and Conda dry runs passed for Windows x64 and Ubuntu x86_64. Windows UI/API checks passed. Ubuntu desktop rendering and physical hardware operation still require target-system validation. ARM and 32-bit systems are not covered by these exports.

---

## Using Studio

### Configure and connect

1. Open **Settings** and select the port and hand side.
2. Check motor IDs and calibrated middle positions against your physical hand.
3. Save and click **Connect**.

To choose a profile at startup:

~~~sh
python main.py --config configs/left_config.json
~~~

Connected control commands can move the hardware automatically. Disconnected gestures and Code-tab `hand` commands can be explored in the preview. Changing the hand side selects the corresponding model and presets; it does not recalibrate the hardware.

### Pose Control

Use **Include** to choose which fingers participate in pose-control presets and movements. Adjust motor offsets or choose a preset, then save a named pose. Clicking a saved pose loads it and commands it when connected.

Angles are motor offsets in degrees relative to calibrated middle positions, not fingertip coordinates or anatomical joint angles.

### Gesture sequences

Add steps, select gestures or saved poses, set delays, and reorder them with **Move up** and **Move down**. Enable **Loop sequence** to repeat until stopped.

### LLM Control

Install [Ollama for Windows or Linux](https://ollama.com/download). See the [official quickstart](https://docs.ollama.com/quickstart), [CLI reference](https://docs.ollama.com/cli), and [model library](https://ollama.com/library).

Start Ollama. If its server is not already running, start it in a separate terminal:

~~~sh
ollama serve
~~~

In another terminal, download **[Qwen2.5 7B](https://ollama.com/library/qwen2.5:7b)**, the app's default model, and check that it is installed. These commands work in Windows PowerShell and Ubuntu terminals:

~~~sh
ollama pull qwen2.5:7b
ollama list
~~~

Optionally test the model directly:

~~~sh
ollama run qwen2.5:7b
~~~

Type `/bye` to exit the terminal chat. The model remains installed for Studio.

In Studio:

1. Open **Settings** and set the Ollama server URL to `http://127.0.0.1:11434` for a local server.
2. Open **LLM Control** and refresh the model list.
3. Select `qwen2.5:7b`, enter an instruction, and inspect the response and preview.

Other installed models can be selected from the dropdown. Download them using `ollama pull` followed by the exact tag from the [model library](https://ollama.com/library). Download models on the machine running the configured Ollama server. Model weights are separate from the Conda environment and are not bundled with this repository.

Connected operation can move the hand; a generated command is not proof of successful physical execution.

### Python editor

The **Code** tab supplies `hand` using the current Studio settings and connection. Click **Run code** or press **Ctrl+Enter**. **Hardware example** loads a complete script; **Open** and **Save as** work with `.py` files.

~~~python
hand.preset("Open")
hand.wait(1.0)
hand.move_finger("index", -20, 20)
print("Saved poses:", list(hand.saved_poses))
~~~

**Stop code**, Escape, and Disconnect request cancellation. Use `hand.wait()` for cancellable delays. Blocking native calls must return before cancellation completes. Scripts are trusted local Python with normal file and network access. Do not open a second controller or start background control threads inside Studio.

### Hand preview

Choose **3D model**, **Kinematics**, or **Both**. Drag to orbit, scroll to zoom, and resize or float the dock. Both views follow the same commanded pose and the hand side selected in Settings.

---

## Python API

Control the hand without launching the GUI:

~~~python
from amazinghand import AmazingHand

print("Available ports:", AmazingHand.available_ports())

with AmazingHand(
    config_path="configs/left_config.json",
    port="COM10",           # Ubuntu example: /dev/ttyUSB0
    baudrate=1_000_000,
    timeout=0.5,            # Seconds
    side="left",           # Match your calibrated hand
    speed=3,               # Controller setting, 1–7
) as hand:
    try:
        hand.preset("Open")
        hand.wait(1.0)
        hand.move_pose({"index": [15, -15], "thumb": [10, -10]})
        hand.wait(1.0)
        print("Measured offsets:", hand.read_pose())
    finally:
        hand.release()
~~~

Use an absolute configuration path when launching from another folder. Close Studio's connection before running a standalone hardware script. The context manager connects on entry and closes the serial port on exit; `release()` explicitly disables torque.

| API | Purpose |
| --- | --- |
| `AmazingHand.available_ports()` | Discover serial ports. |
| `hand.connect()` / `hand.disconnect()` | Manage a standalone connection. |
| `hand.connected` / `hand.config` | Inspect connection state and a copy of configuration. |
| `hand.move_finger(name, m1, m2)` | Move one finger using two motor offsets. |
| `hand.move_pose({...})` | Move only named fingers. |
| `hand.preset(name)` | Execute a built-in pose. |
| `hand.saved_poses` / `hand.saved_pose(name)` | List or execute custom poses. |
| `hand.read_pose()` | Read calibrated motor offsets from hardware. |
| `hand.wait(seconds)` | Wait with cancellation support. |
| `hand.release()` | Disable torque while keeping the serial port open. |

See the [complete Python guide](amazinghand/python_guide.md) and [hardware example](amazinghand/hardware_example.py). After configuring the example, run `python -m amazinghand.hardware_example`.

Motor-range validation does not detect mechanical collisions. Movement commands enable torque on selected motors; releasing torque lets the hand move freely.

---

## Repository layout

~~~text
amazinghand-studio/
├── main.py                    # Desktop entry point
├── README.md
├── requirements.txt           # Windows/Ubuntu dependencies
├── environment.yml            # Conda environment
├── setup.py                   # Python package installation
├── amazinghand/
│   ├── api.py                 # Public AmazingHand interface
│   ├── controller.py          # Serial controller
│   ├── qt_app.py              # Main PyQt5 interface
│   ├── qt_code.py             # Python editor
│   ├── qt_preview.py          # MuJoCo preview
│   ├── hardware_example.py    # Standalone developer example
│   └── python_guide.md        # API and scripting guide
├── assets/mujoco/             # Scenes, meshes, and notices
└── configs/                   # Calibration profiles and settings
~~~

Bundled scenes include their required meshes; no sibling AmazingHand checkout is needed. Local `experiments/`, `publications/`, and `tests/` folders are excluded by `.gitignore` and are not part of the public distribution.

---

## Troubleshooting

| Problem | Check |
| --- | --- |
| Port unavailable or access denied | Confirm the device name; close other applications using it. On Ubuntu, check `dialout` membership. |
| Unexpected finger motion | Check hand side, unique motor IDs, calibrated middle offsets, and finger selection. |
| Preview unavailable | Keep `assets/mujoco/` intact and check OpenGL drivers and the preview error. |
| Qt `xcb` error on Ubuntu | Install the documented system libraries and try `QT_QPA_PLATFORM=xcb`. |
| No LLM models listed | Check the Ollama URL, server availability, and installed models. |
| Import fails in another project | Activate the intended environment and reinstall with `python -m pip install --no-deps -e .`. |
| Script stops slowly | Use `hand.wait()`; native blocking calls are not immediately interruptible. |

Run `python -m pip check` to check dependency consistency.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and contribution guidance.

For bug reports, include your operating system, Python version, selected hand side, reproduction steps, and relevant error output. State whether the problem occurs in preview mode or with physical hardware.

Keep hardware operations separate from Qt widgets, preserve both hand configurations, and document new API methods. Describe how changes were checked and distinguish mock or simulated tests from physical verification. Do not commit credentials, local research data, or generated outputs.

---

## Team

**Developed by Immanuel Jose Valencia · Jeremy Laville · Ryan Rhay Vicerra · Aaron Raymond See.**

### Participating institutions and laboratories

- **Pollen Robotics SAS**, 2 Place Jean Jaurès, 33000 Bordeaux, France.
- **Intelligent Systems Laboratory**, De La Salle University.
- **Department of Biomedical, Manufacturing and Robotics Engineering**, De La Salle University, Philippines.
- **Tactile Optimization and User-Centric Haptics (TOUCH) Lab**, National Chin Yi University of Technology, Taiwan.

---

## Original project and acknowledgments

Credit **Pollen Robotics and the AmazingHand contributors** for the original hand design and upstream resources. The bundled model notices also credit **Steve N'Guyen** for the upstream MuJoCo/Mink demonstration.

| Resource | Link |
| --- | --- |
| Original project | [pollen-robotics/AmazingHand](https://github.com/pollen-robotics/AmazingHand) |
| Build and assembly resources | [Upstream build resources](https://github.com/pollen-robotics/AmazingHand#build-resources) |
| CAD and mechanical files | [AmazingHand CAD](https://github.com/pollen-robotics/AmazingHand/tree/main/cad) |
| Original Python examples | [PythonExample](https://github.com/pollen-robotics/AmazingHand/tree/main/PythonExample) |
| Advanced demonstrations | [AmazingHand Demo](https://github.com/pollen-robotics/AmazingHand/tree/main/Demo) |
| Bundled model provenance | [Model attribution and notices](assets/mujoco/README.md) |

---

## Copyright and licenses

© 2026 AmazingHand Studio contributors.

The original AmazingHand project declares **Apache License 2.0**, with its mechanical design separately licensed under **Creative Commons Attribution 4.0 International**. See the [upstream repository](https://github.com/pollen-robotics/AmazingHand), [bundled Apache license](assets/mujoco/LICENSE-Apache-2.0.txt), and [model notices](assets/mujoco/README.md). Bundled model and mesh files were copied without modification.

These upstream licenses describe the original project and bundled assets. AmazingHand Studio's own code is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution. Third-party packages retain their respective licenses.
