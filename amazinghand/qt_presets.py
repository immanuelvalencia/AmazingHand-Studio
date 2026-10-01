from .config import FINGER_ORDER

def pose_preset(preset_name: str, side="left") -> dict[str, tuple[float, float]]:
    name = preset_name.lower()
    presets: dict[str, dict[str, tuple[float, float]]] = {
        "middle": {finger: (0.0, 0.0) for finger in FINGER_ORDER},
        "open": {finger: (-35.0, 35.0) for finger in FINGER_ORDER},
        "close": {finger: (90.0, -90.0) for finger in FINGER_ORDER},
        "point": {
            "index": (-40.0, 40.0),
            "middle": (90.0, -90.0),
            "ring": (90.0, -90.0),
            "thumb": (90.0, -90.0),
        },
    }
    if side == "left":
        presets.update(
            {
                "spread": {
                    "index": (-60.0, 0.0),
                    "middle": (-35.0, 35.0),
                    "ring": (-4.0, 90.0),
                    "thumb": (-4.0, 90.0),
                },
                "clench": {
                    "index": (0.0, 60.0),
                    "middle": (-35.0, 35.0),
                    "ring": (-70.0, 0.0),
                    "thumb": (-90.0, -4.0),
                },
                "victory": {
                    "index": (-65.0, 15.0),
                    "middle": (-15.0, 65.0),
                    "ring": (90.0, -90.0),
                    "thumb": (90.0, -90.0),
                },
                "pinched": {
                    "index": (90.0, -90.0),
                    "middle": (90.0, -90.0),
                    "ring": (90.0, -90.0),
                    "thumb": (75.0, 5.0),
                },
            }
        )
    else:
        presets.update(
            {
                "spread": {
                    "index": (4.0, 90.0),
                    "middle": (-32.0, 32.0),
                    "ring": (-90.0, -4.0),
                    "thumb": (-90.0, -4.0),
                },
                "clench": {
                    "index": (-60.0, 0.0),
                    "middle": (-35.0, 35.0),
                    "ring": (0.0, 70.0),
                    "thumb": (-4.0, 90.0),
                },
                "victory": {
                    "index": (-15.0, 65.0),
                    "middle": (-65.0, 15.0),
                    "ring": (90.0, -90.0),
                    "thumb": (90.0, -90.0),
                },
                "pinched": {
                    "index": (90.0, -90.0),
                    "middle": (90.0, -90.0),
                    "ring": (90.0, -90.0),
                    "thumb": (0.0, -75.0),
                },
            }
        )
    return presets[name]
