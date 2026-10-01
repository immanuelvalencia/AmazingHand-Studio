"""Embedded Python editor sharing Studio's connection and preview."""
import builtins
from collections import deque
from contextlib import nullcontext
from pathlib import Path
import sys
import threading
import traceback
from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W
from .api import AmazingHand, ScriptStopped
from .qt_syntax import PythonHighlighter

EXAMPLE = """# Studio provides `hand` using your current settings.
# Connect for hardware motion, or leave disconnected for preview.
print("Connected:", hand.connected)
print("Port:", hand.config.serial.port)
print("Baud:", hand.config.serial.baudrate)
print("Side:", hand.config.side)
for name in ("Open", "Point", "Victory", "Middle"):
    print("Pose:", name)
    hand.preset(name)
    hand.wait(1.0)
# Only this finger moves; the other fingers keep their targets.
hand.move_finger("index", -20, 20)
print("Saved poses:", list(hand.saved_poses))
"""


class CodePanel(W.QWidget):
    finished = C.pyqtSignal()

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.cancel = threading.Event()
        self.running = False
        self.messages = deque(maxlen=1000)
        self.path = None
        layout = W.QVBoxLayout(self)
        bar = W.QHBoxLayout()
        self.run_button = W.QPushButton("Run code")
        self.run_button.setProperty("primary", True)
        self.run_button.clicked.connect(self.toggle)
        bar.addWidget(self.run_button)
        for label, slot in (("Open", self.open_file), ("Save as", self.save_file)):
            b = W.QPushButton(label); b.clicked.connect(slot); bar.addWidget(b)
        example = W.QPushButton("Hardware example")
        example.clicked.connect(self.load_hardware_example); bar.addWidget(example)
        bar.addStretch()
        self.mode = W.QLabel("Uses current connection and hand settings")
        bar.addWidget(self.mode)
        layout.addLayout(bar)
        split = W.QSplitter(C.Qt.Vertical)
        self.editor = W.QPlainTextEdit(EXAMPLE)
        self.highlighter = PythonHighlighter(self.editor.document())
        self.editor.setFont(G.QFontDatabase.systemFont(G.QFontDatabase.FixedFont))
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        self.editor.setLineWrapMode(W.QPlainTextEdit.NoWrap)
        self.editor.setPlaceholderText("Write Python here. The hand object is ready to use.")
        split.addWidget(self.editor)
        bottom = W.QTabWidget()
        self.output = W.QPlainTextEdit(); self.output.setReadOnly(True)
        self.output.document().setMaximumBlockCount(2000)
        bottom.addTab(self.output, "Output")
        guide = W.QTextBrowser()
        guide.setMarkdown(Path(__file__).with_name("python_guide.md").read_text(encoding="utf-8"))
        bottom.addTab(guide, "Python guide")
        split.addWidget(bottom); split.setSizes([400, 200]); layout.addWidget(split)
        self.finished.connect(self.finish)
        self.timer = C.QTimer(self); self.timer.timeout.connect(self.flush); self.timer.start(50)
        W.QShortcut(G.QKeySequence("Ctrl+Return"), self, activated=self.toggle, context=C.Qt.WidgetWithChildrenShortcut)

    def flush(self):
        if self.messages:
            parts = []
            while self.messages:
                parts.append(self.messages.popleft())
            cursor = self.output.textCursor(); cursor.movePosition(G.QTextCursor.End)
            cursor.insertText("".join(parts)); self.output.setTextCursor(cursor)

    def load_hardware_example(self):
        self.editor.setPlainText(Path(__file__).with_name("hardware_example.py").read_text(encoding="utf-8"))
        self.path = None

    def open_file(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "Open Python script", "", "Python (*.py)")
        if path:
            try:
                code = Path(path).read_text(encoding="utf-8")
                self.editor.setPlainText(code); self.path = path
            except (OSError, UnicodeError) as exc:
                W.QMessageBox.warning(self, "Open failed", str(exc))

    def save_file(self):
        path, _ = W.QFileDialog.getSaveFileName(self, "Save Python script", self.path or "hand_script.py", "Python (*.py)")
        if path:
            try:
                Path(path).write_text(self.editor.toPlainText(), encoding="utf-8"); self.path = path
            except OSError as exc:
                W.QMessageBox.warning(self, "Save failed", str(exc))

    def toggle(self):
        if self.running:
            self.studio.stop(); return
        host = self.studio
        if host.busy() or host.disconnect_pending or host.connection_pending or host.closing:
            host.statusBar().showMessage("Wait for the current action to finish."); return
        try:
            code = compile(self.editor.toPlainText(), self.path or "<Studio code>", "exec")
        except SyntaxError:
            self.output.setPlainText(traceback.format_exc()); return
        self.cancel.clear(); self.output.clear(); self.running = True
        host.live_timer.stop(); host.controller.resume_motion()
        self.tab_states = [host.tabs.isTabEnabled(i) for i in range(host.tabs.count())]
        for i in range(host.tabs.count()):
            host.tabs.setTabEnabled(i, host.tabs.widget(i) is self)
        self.run_button.setText("Stop code"); host.danger_style(self.run_button, True)
        self.mode.setText("Running on hardware" if host.controller.connected else "Running preview only")
        config = host.config
        def preview(pose):
            for name, angles in pose.items():
                for sid, middle, angle in zip(config.fingers[name].servos, config.fingers[name].middle, angles):
                    host.events.put(("servo", (sid, middle + angle)))
        hand = AmazingHand(controller=host.controller, preview=preview, cancel=self.cancel)
        def emit_print(*args, sep=" ", end="\n", file=None, flush=False):
            if file is not None:
                return builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            self.messages.append((sep.join(str(a) for a in args) + end)[:16000])
        def trace(frame, event, arg):
            if frame.f_code.co_filename == code.co_filename:
                frame.f_trace_opcodes = True
                if self.cancel.is_set():
                    raise ScriptStopped()
                return trace
            return None
        def execute():
            try:
                with host.controller.exclusive_control() if host.controller.connected else nullcontext():
                    namespace = {"__name__": "__main__", "__file__": self.path or "<Studio code>",
                                 "hand": hand, "__builtins__": dict(vars(builtins), print=emit_print)}
                    sys.settrace(trace)
                    exec(code, namespace)
                self.messages.append("\nFinished.\n")
            except ScriptStopped:
                self.messages.append("\nStopped.\n")
            except BaseException:
                self.messages.append(traceback.format_exc())
            finally:
                sys.settrace(None)
                self.finished.emit()
        host.work("Python script", execute)

    def request_stop(self):
        if self.running:
            self.cancel.set(); self.run_button.setText("Stopping..."); self.run_button.setEnabled(False)

    def finish(self):
        self.running = False; self.flush()
        self.run_button.setText("Run code"); self.run_button.setEnabled(True)
        self.studio.danger_style(self.run_button, False)
        self.mode.setText("Uses current connection and hand settings")
        for i, enabled in enumerate(self.tab_states):
            self.studio.tabs.setTabEnabled(i, enabled)
