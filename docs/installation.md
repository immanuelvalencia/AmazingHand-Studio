# Install AmazingHand Studio

These exports target **Python 3.11, Windows 10/11 x64, and Ubuntu 22.04/24.04 x86_64**. ARM, 32-bit systems, and headless GUI rendering are not covered by this export.

| File | Use |
| --- | --- |
| `environment.yml` | Full Conda environment: PyQt5 app, hardware, preview, and report dependencies. |
| `requirements.txt` | The same full application dependencies for pip. |

The portable files pin direct dependencies to the working environment. Dependencies such as Qt's native runtime resolve separately for each operating system; these are not complete transitive lockfiles. There are no machine-specific paths, CUDA packages, Windows-only packages, or Conda build strings. Keep the installation files together because the environments reference their adjacent requirements files.

## Full application with Conda (Windows and Ubuntu)

Install Conda for your operating system, clone the repository, and run these commands **from the repository root**:

```sh
conda env create -f environment.yml
conda activate amazinghand-studio
python -m pip install --no-deps -e .
python -m pip check
python main.py
```

If that environment name already exists, create a separate one using `conda env create -n amazinghand-studio-new -f environment.yml`, then activate `amazinghand-studio-new`.

To use an existing Python 3.11 Conda environment instead:

```sh
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -m pip check
python main.py
```

This does not require PyTorch. Qt is supplied by PyQt5 through pip; do not mix another Qt binding or Conda Qt distribution into this environment.

## Standalone developer library

```sh
conda env create -f environment.yml
conda activate amazinghand-studio
python -m pip install --no-deps -e .
python -c "from amazinghand import AmazingHand; print(AmazingHand.available_ports())"
```

The editable install lets other projects import `amazinghand` while this checkout remains at the same location. Reinstall if you move it. Configure your port and calibration before running `python -m amazinghand.hardware_example`. See [the Python guide](../amazinghand/python_guide.md) for the API, connection setup, and cleanup. The same environment supports both standalone scripts and the full app.

## Windows prerequisites

Use a 64-bit Conda installation. Install the USB serial adapter's manufacturer driver if the device does not appear in Device Manager. Select its actual `COM` port in Settings or your script. Baud is fixed at **1,000,000**. Close other applications using the same port. The preview needs functioning OpenGL graphics drivers.

## Ubuntu prerequisites

For the desktop GUI and MuJoCo preview, install the system graphics libraries:

```sh
sudo apt update
sudo apt install -y libgl1 libegl1 libglfw3 libdbus-1-3 libfontconfig1 \
  libx11-xcb1 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxcb-shape0
```

Run the app from a desktop session with OpenGL drivers installed. On Wayland, if Qt reports a platform-plugin error, run with the X11 backend through XWayland:

```sh
QT_QPA_PLATFORM=xcb python main.py
```

For hardware access, add your user to the serial-device group and **log out and log back in**:

```sh
sudo usermod -aG dialout "$USER"
```

Use the actual device path, usually `/dev/ttyUSB0` or `/dev/ttyACM0`, instead of a Windows COM port. Port discovery is available through `AmazingHand.available_ports()`. Standalone hardware scripts do not use the desktop graphics packages.

System graphics requirements are separate from pip/Conda: see [Qt 5 dependencies](https://wiki.qt.io/Qt5_dependencies) and [MuJoCo Python installation](https://mujoco.readthedocs.io/en/stable/python.html#installation).

## Assets and LLM Control

Keep `assets/mujoco/` and `configs/` with the checkout. Both hand scenes and meshes are bundled; the original AmazingHand repository is not required at runtime. Use `python main.py --config configs/left_config.json` to choose a profile. Select the corresponding side and calibration in Settings.

LLM Control needs a separately installed, running Ollama service and downloaded model, or access to the configured Ollama server. Model weights and that service are not part of the Python environment.

## Validation scope

The existing Windows environment passes `pip check` and the app's focused UI/API tests. Binary-only dependency resolution was checked for both Windows x64 and Ubuntu x86_64 with Python 3.11. Conda base-environment solves are checked separately from pip resolution. Cross-platform resolution checks package availability; it does not prove Ubuntu desktop rendering, USB permissions, or physical hardware operation. Those require running on the target machine.
