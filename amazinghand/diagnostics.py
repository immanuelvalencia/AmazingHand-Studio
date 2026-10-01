from __future__ import annotations

import importlib.util
import sys


def dependency_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [port.device for port in list_ports.comports()]


def diagnostics_lines() -> list[str]:
    ports = list_serial_ports()
    return [
        f"Python: {sys.executable}",
        f"rustypot installed: {dependency_available('rustypot')}",
        f"pyserial installed: {dependency_available('serial')}",
        f"mujoco installed: {dependency_available('mujoco')}",
        f"serial ports: {', '.join(ports) if ports else 'none detected'}",
    ]
