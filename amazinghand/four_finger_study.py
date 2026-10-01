"""Independent four-finger study specifications, never inferred by the parser."""
from copy import deepcopy
from itertools import combinations

VERSION = "four-finger-200-v1"
LABEL = "Four-finger study · 200"
FINGERS = ("index", "middle", "ring", "thumb")


def study_cases():
    rows = []
    initial = dict(zip(FINGERS, (28, 44, 61, 77)))

    def pair(category, direct, paraphrase, steps, skill, **context):
        number = len(rows) // 2 + 1
        for suffix, wording, command in (("D", "direct", direct), ("P", "paraphrase", paraphrase)):
            rows.append({"id": f"FF{number:03}{suffix}", "suite_version": VERSION,
                         "pair_id": f"FF{number:03}", "category": category, "wording": wording,
                         "command": command, "skill": skill,
                         "expected": {"steps": deepcopy(steps)}, **deepcopy(context)})

    # Symbolic gestures, not claims of human-equivalent anatomy or object grasping.
    gestures = [
        ("open", "open the hand", "Show the open-hand gesture with all four fingers extended"),
        ("close", "close the hand", "Show the closed-hand gesture with all four fingers curled"),
        ("point", "point", "Use the pointing gesture: extend the index and curl the other three fingers"),
        ("peace", "show peace", "Make the V sign with index and middle extended and separated; curl ring and thumb"),
        ("yes", "show yes", "Use the thumb-up gesture, with index, middle, and ring curled"),
        ("no", "show no", "Use the no gesture: wag the index side to side three times, with the other fingers curled"),
    ]
    for action, direct, paraphrase in gestures:
        pair("pose", direct, paraphrase, [{"action": action}], "four_finger_gesture", gesture=action)
        pair("pose", direct + " at speed 2 for 1 second",
             paraphrase + "; use motor speed 2 and a one-second hold",
             [{"action": action, "speed": 2, "hold_seconds": 1}], "gesture_parameters", gesture=action)

    # Eight paired tasks per finger: four absolute, two relative, two contextual.
    for finger in FINGERS:
        for value in (0, 100, 32, 68):
            pair("finger", f"set {finger} to {value}%",
                 f"Change only {finger} to {value} percent opening; leave the other three as they are",
                 [{"action": "pose", "fingers": {finger: value}}], "absolute_target", targets=initial, tested_finger=finger)
        for verb, delta in (("open", 13), ("close", 17)):
            value = initial[finger] + (delta if verb == "open" else -delta)
            pair("finger", f"{verb} {finger} by {delta}%",
                 f"{'Increase' if verb == 'open' else 'Decrease'} only {finger}'s opening by {delta} percentage points",
                 [{"action": "pose", "fingers": {finger: value}}], "relative_target", targets=initial, tested_finger=finger)
        previous = {"steps": [{"action": "pose", "fingers": {finger: initial[finger]}}]}
        pair("finger", "set it to 57%", "Put the finger from the previous command at 57 percent opening",
             [{"action": "pose", "fingers": {finger: 57}}], "context_absolute", targets=initial,
             previous=previous, tested_finger=finger)
        pair("finger", "close that finger by 9%", "Reduce the opening of the previously commanded finger by nine percentage points",
             [{"action": "pose", "fingers": {finger: initial[finger]-9}}], "context_relative", targets=initial,
             previous=previous, tested_finger=finger)

    # Every unordered two-finger combination: simultaneous assignment and ordered swap.
    for a, b in combinations(FINGERS, 2):
        first = {"action": "pose", "fingers": {a: 36, b: 64}}
        second = {"action": "pose", "fingers": {a: 64, b: 36}}
        pair("sequence", f"set {a} to 36% and {b} to 64%",
             f"In one pose, set {a} at 36 percent and {b} at 64 percent opening; do not change the other two",
             [first], "simultaneous_pair", targets=initial, tested_pair=[a, b])
        pair("sequence", f"set {a} to 36% and {b} to 64% then set {a} to 64% and {b} to 36%",
             f"First set {a} to 36% and {b} to 64% together; then swap their openings, leaving the other fingers unchanged",
             [first, second], "ordered_pair_swap", targets=initial, tested_pair=[a, b])
    for finger in FINGERS:
        pair("sequence", f"set {finger} to 24% then set {finger} to 76% then set {finger} to 24%",
             f"Move only {finger} through these openings in order: 24%, 76%, 24%",
             [{"action": "pose", "fingers": {finger: v}} for v in (24, 76, 24)], "return_trajectory", targets=initial)
        pair("sequence", f"set {finger} to 39% then open it by 16% then close it by 8%",
             f"Set {finger} to 39% opening, increase that same finger by 16 percentage points, then decrease it by 8 points",
             [{"action": "pose", "fingers": {finger: v}} for v in (39, 55, 47)], "state_propagation", targets=initial)
    for actions in (("open", "point", "close"), ("close", "peace", "open"),
                    ("yes", "open", "yes"), ("no", "close", "point")):
        names = {"open": "open the hand", "close": "close the hand", "point": "point",
                 "peace": "show peace", "yes": "show yes", "no": "show no"}
        pair("sequence", " then ".join(names[a] for a in actions),
             "Perform these gestures in order: " + ", ".join(actions) + ". Use a one-second hold for each",
             [{"action": a, "hold_seconds": 1} for a in actions], "gesture_order")
        # Explicit timing on each direct step avoids accidental final-step scope.
        rows[-2]["command"] = " then ".join(names[a] + " for 1 second" for a in actions)

    # Twenty arithmetic intents. Above four is explicitly a temporal code.
    math_tasks = [("6 - 6", "subtract six from six", 0), ("8 / 8", "divide eight by eight", 1),
                  ("9 - 7", "subtract seven from nine", 2), ("1 + 2", "add one and two", 3),
                  ("2 * 2", "multiply two by two", 4), ("(3 + 5) / 2 - 4", "add three and five, divide by two, then subtract four", 0),
                  ("7 - 2 * 3", "subtract the product of two and three from seven", 1),
                  ("(8 - 2) / 3", "subtract two from eight, then divide by three", 2),
                  ("12 / 4", "divide twelve by four", 3), ("9 - 5", "subtract five from nine", 4),
                  ("11 - 6", "subtract six from eleven", 5), ("2 * 3", "multiply two by three", 6),
                  ("16 / 2", "divide sixteen by two", 8), ("14 - 5", "subtract five from fourteen", 9),
                  ("(2 + 3) * 2", "add two and three, then multiply by two", 10),
                  ("15 - 3", "subtract three from fifteen", 12), ("6 + 7", "add six and seven", 13),
                  ("4 * 4", "multiply four by four", 16), ("20 - 3", "subtract three from twenty", 17),
                  ("4 * 5", "multiply four by five", 20)]
    for expression, words, result in math_tasks:
        convention = ("Display the result as one four-finger count" if result <= 4 else
                      "Display the result over time in groups of at most four raised fingers, closing the hand between groups")
        pair("arithmetic", expression, f"{words.capitalize()}. {convention}",
             [{"action": "number", "number": result}], "single_pose_count" if result <= 4 else "temporal_count",
             display_convention="index-middle-ring-thumb order; zero closed; groups of four separated by close")

    invalid = [
        ("open it by 12%", "Increase that finger by twelve percentage points", "missing_referent", {}),
        ("repeat that", "Repeat the previous hand command", "missing_history", {}),
        ("set pinky to 50%", "Set the little finger halfway open", "absent_finger", {}),
        ("rotate the wrist by 20 degrees", "Turn the wrist twenty degrees", "absent_wrist", {}),
    ]
    for finger in FINGERS:
        invalid.append((f"set {finger} to 108%", f"Set only {finger} to 108 percent opening", "outside_range", {}))
    invalid += [
        ("set index to 35% and thumb to -4%", "Set index to 35 percent and thumb to negative four percent in one pose", "invalid_compound", {}),
        ("open the hand then set ring to 110%", "First open the hand, then put ring at 110 percent opening", "invalid_sequence", {}),
        ("point at speed 0", "Use the pointing gesture at motor speed zero", "invalid_speed", {}),
        ("close the hand for 6 seconds", "Hold the closed-hand gesture for six seconds", "invalid_duration", {}),
    ]
    for direct, paraphrase, skill, context in invalid:
        pair("robustness", direct, paraphrase, [{"action": "unknown"}], skill,
             expected_behavior="no_motion", **context)
    assert len(rows) == 200
    return rows


def materialize():
    """Freeze references independently of parser predictions; never move hardware."""
    import json
    from .experiments import ROOT, validate_dataset, digest
    from .automated_study import freeze_validation
    root = ROOT / "configs/experiments/four_finger_200"
    root.mkdir(parents=True, exist_ok=True)
    cases = validate_dataset(study_cases())
    protocol = json.loads((root.parent / "study_protocol.json").read_text(encoding="utf-8"))
    frozen = freeze_validation(cases, protocol)
    for name, data in (("commands.json", cases), ("study_protocol.json", protocol),
                       ("validation_references.json", frozen)):
        (root / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"version": VERSION, "dataset_sha256": digest(cases), **frozen["coverage"]}, indent=2))


if __name__ == "__main__":
    materialize()
