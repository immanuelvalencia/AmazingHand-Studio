# Contributing to AmazingHand Studio

## Set up

Follow the [README](README.md#installation), then create a branch for your change. Use Python 3.11 and the shared requirements on Windows or Ubuntu.

## Report a problem

Include your operating system, Python version, reproduction steps, error output, selected hand side, and whether hardware was connected. Remove credentials and personal data from attachments.

## Submit a change

Keep changes focused and explain the problem, resulting behavior, and validation. Keep hardware operations separate from Qt widgets. Preserve both hand configurations and update the Python guide for API changes. Identify testing performed with mocks or simulation separately from physical tests.

Run `python -m pip check` and `python -m compileall -q amazinghand main.py`. Exercise affected UI behavior in disconnected preview mode before hardware testing. CI checks dependency installation, Python imports, and bundled model loading on Windows and Ubuntu; it does not control hardware.

Do not commit local research data, manuscripts, credentials, generated outputs, or machine-specific calibration trials. Preserve upstream asset notices.
