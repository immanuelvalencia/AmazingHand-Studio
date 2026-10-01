"""Run with python -m amazinghand.hardware_example, or open in Studio's Code tab."""
from amazinghand import AmazingHand

# Change these for your calibrated hand before running from a terminal.
CONFIG_PATH = "configs/left_config.json"  # Use an absolute path from another folder.
PORT = "COM10"                          # Linux example: /dev/ttyUSB0
BAUDRATE = 1_000_000                     # Required by the AmazingHand controller.
TIMEOUT = 0.5                           # Serial timeout in seconds.
SIDE = "left"                           # "left" or "right"; use matching calibration.
SPEED = 3                               # Controller speed setting: 1 through 7.


def perform_sequence(hand):
    print("Port:", hand.config.serial.port)
    print("Baud:", hand.config.serial.baudrate)
    print("Side:", hand.config.side)
    for cycle in range(3):
        print("Cycle", cycle + 1)
        hand.preset("Open")
        hand.wait(1.0)
        hand.move_pose({"index": [15, -15], "thumb": [10, -10]})
        hand.wait(1.0)
    hand.preset("Middle")
    hand.wait(1.0)
    if hand.connected:
        print("Measured motor offsets:", hand.read_pose())
    print("Saved poses:", list(hand.saved_poses))
    # hand.saved_pose("My pose")  # Use a name present in your configuration.


if __name__ == "__main__":
    if "hand" in globals():
        # Studio supplies `hand`; its Settings override the constants above.
        # Connect in Studio for hardware, or leave disconnected for preview.
        perform_sequence(hand)
    else:
        print("Available ports:", AmazingHand.available_ports())
        with AmazingHand(config_path=CONFIG_PATH, port=PORT, baudrate=BAUDRATE,
                         timeout=TIMEOUT, side=SIDE, speed=SPEED) as hand:
            try:
                perform_sequence(hand)
            finally:
                hand.release()  # Disable torque; the context then closes the port.
