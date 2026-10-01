"""AmazingHand Studio calibration, control, and demo tools."""

from .config import HandConfig, load_config, save_config
from .controller import AmazingHandController
from .gestures import AmazingHandGestures
def __getattr__(name):
    if name == "MuJoCoBridge":
        from .simulation import MuJoCoBridge
        return MuJoCoBridge
    raise AttributeError(name)


__all__ = [
    "AmazingHandController",
    "AmazingHandGestures",
    "HandConfig",
    "MuJoCoBridge",
    "load_config",
    "save_config",
]

from .api import AmazingHand
__all__.append("AmazingHand")
