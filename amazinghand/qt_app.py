"""AmazingHand Studio Qt application. Run: python -m amazinghand.qt_app."""
import argparse
from copy import deepcopy
from pathlib import Path
import queue
import threading
from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W
from .config import DEFAULT_CONFIG_PATH, SERIAL_BAUDRATE, FINGER_ORDER, load_config, save_config
from .controller import AmazingHandController, TORQUE_FREE
from .gestures import AmazingHandGestures
from .assistant_engine import AssistantEngine, load_assistant_settings, save_assistant_settings
from .diagnostics import list_serial_ports
from .chat.ollama_client import OllamaClient
from .qt_code import CodePanel
from .qt_preview import Preview
from .qt_presets import pose_preset
from .sequences import GESTURES, prepare_steps, execute_steps

STYLE = '''
QMainWindow,QDialog {background:#f3f6fb;} QWidget {font-family:'Segoe UI';font-size:9pt;color:#233148;}
QGroupBox {background:white;border:1px solid #dce3ed;border-radius:8px;margin-top:12px;padding:6px;}
QGroupBox::title {subcontrol-origin:margin;left:12px;padding:0 5px;font-weight:600;}
QPushButton {background:#e9eef6;border:1px solid #cbd5e1;border-radius:5px;padding:4px 8px;min-height:18px;}
QPushButton:hover {background:#dbe7f8;border-color:#8aaade;}
QPushButton:pressed,QPushButton:checked {background:#dbeafe;color:#1e40af;border:1px solid #3b82f6;}
QPushButton:disabled {color:#94a3b8;background:#f1f5f9;}
QPushButton[primary="true"] {background:#2563eb;color:white;border-color:#2563eb;}
QPushButton[danger="true"] {background:#dc2626;color:white;border-color:#dc2626;}
QPushButton[danger="true"]:hover {background:#b91c1c;border-color:#b91c1c;}
QPushButton[danger="true"]:disabled {background:#f1b6b6;color:#7f1d1d;border-color:#f1b6b6;}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QTextEdit {background:white;border:1px solid #cbd5e1;border-radius:4px;padding:3px;}
QTabWidget::pane {border:0;} QTabBar::tab {padding:6px 12px;background:#e8edf5;}
QTabBar::tab:selected {background:white;color:#1d4ed8;} QSplitter::handle {background:#dce3ed;}
QScrollArea {border:0;} QDockWidget::title {padding:6px;font-weight:600;background:#e8edf5;}
'''

def button(text, callback, primary=False):
    result=W.QPushButton(text);result.setProperty('primary',primary);result.clicked.connect(callback);return result

def scroll(widget):
    area=W.QScrollArea();area.setWidgetResizable(True);area.setWidget(widget);return area

class Studio(W.QMainWindow):
    def __init__(self, config_path=None):
        super().__init__();self.setWindowTitle('AmazingHand Studio · Qt');self.resize(1180,760);self.setMinimumSize(720,520)
        self.path=Path(config_path or DEFAULT_CONFIG_PATH);self.config=load_config(self.path)
        self.controller=AmazingHandController(self.config);self.gestures=AmazingHandGestures(self.controller)
        self.pose={name:[0.,0.] for name in FINGER_ORDER};self.events=queue.Queue();self.engine=AssistantEngine(self.events)
        self.sequence_running=False
        self.sequence_cancel=threading.Event()
        self.settings=load_assistant_settings();self.worker=None;self.loading=False;self.closing=False;self.selected=None;self.disconnect_pending=False;self.connection_pending=False
        self.controller.set_position_observer(lambda sid,value:self.events.put(('servo',(sid,value))))
        root=W.QWidget();self.setCentralWidget(root);layout=W.QVBoxLayout(root)
        bar=W.QHBoxLayout();title=W.QLabel('AmazingHand Studio');title.setStyleSheet('font-size:14pt;font-weight:600')
        bar.addWidget(title);bar.addStretch();self.connection=W.QLabel('Disconnected');bar.addWidget(self.connection)
        self.connect_button=button('Connect',self.toggle_connection,True);bar.addWidget(self.connect_button)
        self.settings_button=button('Settings',self.open_settings);bar.addWidget(self.settings_button);bar.addWidget(button('About',self.open_about));layout.addLayout(bar)
        self.tabs=W.QTabWidget();layout.addWidget(self.tabs,1)
        self.build_pose();self.build_gestures();self.build_chat()
        self.code_panel=CodePanel(self);self.tabs.addTab(self.code_panel,"Code")
        self.preview=Preview(self);dock=W.QDockWidget('Hand preview',self);dock.setObjectName('hand-preview')
        dock.setFeatures(W.QDockWidget.DockWidgetMovable|W.QDockWidget.DockWidgetFloatable)
        dock.setWidget(self.preview);self.addDockWidget(C.Qt.RightDockWidgetArea,dock);self.resizeDocks([dock],[360],C.Qt.Horizontal)
        self.statusBar().showMessage('Ready · preview only until connected')
        self.live_timer=C.QTimer(self);self.live_timer.setSingleShot(True);self.live_timer.timeout.connect(self.move_pose)
        self.poll=C.QTimer(self);self.poll.timeout.connect(self.drain);self.poll.start(40)
        W.QShortcut(G.QKeySequence('Escape'),self,activated=self.stop)
        C.QTimer.singleShot(100,self.preview.request)
        self.model_request=0
        C.QTimer.singleShot(0,self.refresh_models)
    def busy(self):return self.engine.active or (self.worker is not None and self.worker.is_alive())
    def work(self, label, action):
        if self.busy():self.statusBar().showMessage('Wait for the current action to finish.');return
        self.statusBar().showMessage(label)
        def run():
            try:
                action()
                if label!='Sequence':self.events.put(('status',label+' complete'))
            except Exception as exc:self.events.put(('connection_error' if label=='Connect' else 'error',str(exc)))
            finally:self.events.put(('hardware_done',None))
        self.worker=threading.Thread(target=run,daemon=True);self.worker.start()
    def toggle_connection(self):
        if self.connection_pending:return
        if self.controller.connected:
            self.stop(release=False);self.disconnect_pending=True
        else:
            if self.busy():return
            self.connection_pending=True
            self.connection.setText('Connecting…')
            self.connect_button.setEnabled(False)
            def connect():self.controller.connect();self.controller.resume_motion()
            self.work('Connect',connect)
    def stop(self, release=True):
        if hasattr(self,"code_panel"):self.code_panel.request_stop()
        self.sequence_cancel.set()
        if hasattr(self,'sequence_button'):self.sync_sequence_button()
        self.live_timer.stop();self.controller.block_motion();self.engine.stop()
        if release and self.controller.connected:
            # Release runs once the active worker acknowledges cancellation.
            self.events.put(('release',None))
    def build_pose(self):
        page=W.QWidget();layout=W.QVBoxLayout(page);layout.setContentsMargins(4,4,4,4);layout.setSpacing(6)
        actions=W.QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button('Move pose',self.move_pose,True));layout.addLayout(actions)
        presets=W.QGroupBox('Presets');grid=W.QGridLayout(presets);grid.setContentsMargins(4,8,4,4);grid.setSpacing(4)
        for i,name in enumerate(('Middle','Open','Close','Spread','Clench','Point','Victory','Pinched')):
            grid.addWidget(button(name,lambda _,n=name:self.apply_preset(n)),i//4,i%4)
        layout.addWidget(presets);self.fingers={};self.angles={};self.links={}
        finger_actions=W.QHBoxLayout();finger_actions.addWidget(W.QLabel('Fingers'));finger_actions.addStretch()
        self.finger_selection_button=button('Select none',self.toggle_finger_selection)
        finger_actions.addWidget(self.finger_selection_button);layout.addLayout(finger_actions)
        finger_grid=W.QGridLayout();finger_grid.setSpacing(6);layout.addLayout(finger_grid)
        for index,name in enumerate(FINGER_ORDER):
            box=W.QGroupBox(name.title());v=W.QVBoxLayout(box);v.setContentsMargins(6,10,6,6);v.setSpacing(4);head=W.QHBoxLayout()
            toggle=button('Include',lambda _:self.edited());toggle.setCheckable(True);toggle.setChecked(True);self.fingers[name]=toggle
            head.addWidget(toggle);head.addStretch()
            link=W.QComboBox();link.addItems(['Independent','Same','Inverse']);link.setToolTip('Relationship between the two motor angles');self.links[name]=link;head.addWidget(link);v.addLayout(head)
            row=W.QGridLayout();self.angles[name]=[]
            for motor in range(2):
                row.addWidget(W.QLabel(f'M{motor+1} (°)'),motor,0)
                slider=W.QSlider(C.Qt.Horizontal);slider.setRange(-1000,1000);row.addWidget(slider,motor,1)
                spin=W.QDoubleSpinBox();spin.setRange(-100,100);spin.setDecimals(1);spin.setSingleStep(1);row.addWidget(spin,motor,2)
                slider.valueChanged.connect(lambda val,s=spin:s.setValue(val/10))
                spin.valueChanged.connect(lambda val,s=slider:s.setValue(round(val*10)))
                spin.valueChanged.connect(lambda val,n=name,m=motor:self.angle_changed(n,m,val));self.angles[name].append(spin)
            v.addLayout(row);finger_grid.addWidget(box,index//2,index%2)
        save=W.QHBoxLayout();self.pose_name=W.QLineEdit();self.pose_name.setPlaceholderText('Pose name')
        save.addWidget(self.pose_name);save.addWidget(button('Save pose',self.save_pose));layout.addLayout(save)
        library=W.QGroupBox('Saved poses');lv=W.QVBoxLayout(library);lv.setContentsMargins(4,8,4,4);lv.setSpacing(4);self.search=W.QLineEdit();self.search.setPlaceholderText('Search saved poses')
        self.search.textChanged.connect(self.refresh_library);lv.addWidget(self.search)
        tiles_widget=W.QWidget();self.tiles=W.QGridLayout(tiles_widget);self.tiles.setContentsMargins(0,0,0,0)
        tiles_scroll=scroll(tiles_widget);tiles_scroll.setMinimumHeight(38);tiles_scroll.setMaximumHeight(100);lv.addWidget(tiles_scroll);row=W.QHBoxLayout()
        row.addStretch();self.delete_pose_button=button('Delete selected',self.delete_pose);self.delete_pose_button.setProperty('danger',True);row.addWidget(self.delete_pose_button);lv.addLayout(row)
        layout.addWidget(library);layout.addStretch();self.tabs.addTab(scroll(page),'Pose Control');self.refresh_library()
    def toggle_finger_selection(self):
        self.select_fingers(not all(b.isChecked() for b in self.fingers.values()))

    def select_fingers(self,value):
        for b in self.fingers.values():b.setChecked(value)
        self.edited()
    def angle_changed(self,name,motor,value):
        if self.loading:return
        self.loading=True
        mode=self.links[name].currentText()
        if mode!='Independent':self.angles[name][1-motor].setValue(value if mode=='Same' else -value)
        self.loading=False;self.edited()
    def edited(self):
        if self.loading:return
        self.finger_selection_button.setText('Select none' if all(b.isChecked() for b in self.fingers.values()) else 'Select all')
        for name,values in self.angles.items():self.pose[name]=[s.value() for s in values]
        if hasattr(self,'preview'):self.preview.request()
        if self.controller.connected and not self.disconnect_pending and not self.engine.active:self.live_timer.start(90)
    def apply_preset(self, name):
        selected=[finger for finger,toggle in self.fingers.items() if toggle.isChecked()]
        if not selected:
            self.statusBar().showMessage('Include at least one finger before applying a preset.')
            return
        preset=pose_preset(name,self.config.side)
        self.loading=True
        try:
            for finger in selected:
                for widget,value in zip(self.angles[finger],preset[finger]):widget.setValue(value)
        finally:self.loading=False
        self.pose_name.setText(name.lower());self.edited()

    def apply_pose(self,pose,name=''):
        self.loading=True
        for finger,widgets in self.angles.items():
            self.fingers[finger].setChecked(finger in pose)
            if finger in pose:
                for widget,value in zip(widgets,pose[finger]):widget.setValue(value)
        self.loading=False;self.pose_name.setText(name);self.edited()
    def selected_pose(self):return {n:list(self.pose[n]) for n,b in self.fingers.items() if b.isChecked()}
    def move_pose(self):
        if self.disconnect_pending or self.closing:return
        if not self.controller.connected:self.statusBar().showMessage('Connect the hand to move it.');return
        pose=self.selected_pose()
        if not pose:return
        if self.busy():
            if not self.engine.active:self.live_timer.start(90)
            return
        self.controller.resume_motion()
        self.work('Move pose',lambda:self.gestures.move_powered_pose(pose))
    def save_pose(self):
        name=self.pose_name.text().strip();pose=self.selected_pose()
        if not name or not pose:self.statusBar().showMessage('Enter a name and include at least one finger.');return
        self.config.poses[name]=pose
        try:save_config(self.config,self.path)
        except Exception as exc:self.statusBar().showMessage(str(exc));return
        self.selected=name;self.refresh_library();self.statusBar().showMessage('Pose saved')
    def refresh_library(self):
        while self.tiles.count():
            item=self.tiles.takeAt(0);item.widget().deleteLater()
        names=[n for n in sorted(self.config.poses) if self.search.text().casefold() in n.casefold()]
        for i,name in enumerate(names):
            b=button(name,lambda _,n=name:self.choose_pose(n));b.setCheckable(True);b.setChecked(name==self.selected)
            self.tiles.addWidget(b,i//2,i%2)
        if not names:self.tiles.addWidget(W.QLabel('No matching poses' if self.config.poses else 'Save your first pose above.'),0,0)
    def choose_pose(self,name):self.selected=name;self.apply_pose(self.config.poses[name],name);self.refresh_library()
    def delete_pose(self):
        if self.selected not in self.config.poses:return
        self.config.poses.pop(self.selected);save_config(self.config,self.path);self.selected=None;self.refresh_library()
    def build_chat(self):
        page=W.QWidget();v=W.QVBoxLayout(page);row=W.QHBoxLayout();self.model=W.QComboBox();self.model.setMinimumContentsLength(16);self.model.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(W.QLabel('Model'));row.addWidget(self.model,1)
        self.refresh_models_button=button('Refresh models',self.refresh_models);row.addWidget(self.refresh_models_button);v.addLayout(row)
        self.model_status=W.QLabel('Loading available models…');self.model_status.setWordWrap(True);v.addWidget(self.model_status)
        self.model.activated.connect(self.save_model_selection)
        self.transcript=W.QTextEdit();self.transcript.setReadOnly(True);v.addWidget(self.transcript,1)
        self.draft=W.QLabel();self.draft.setWordWrap(True);v.addWidget(self.draft)
        row=W.QHBoxLayout();self.message=W.QLineEdit();self.message.setPlaceholderText('Ask the hand assistant…');self.message.returnPressed.connect(self.send_chat)
        row.addWidget(self.message,1);row.addWidget(button('Send',self.send_chat,True));row.addWidget(button('Cancel',self.engine.stop));v.addLayout(row)
        self.tabs.addTab(page,'LLM Control')
    def refresh_models(self):
        self.model_request += 1
        request=self.model_request;url=self.settings['ollama_url']
        self.model_status.setText('Loading available models…')
        self.refresh_models_button.setEnabled(False)
        def fetch():
            try:
                with OllamaClient(url,10) as client:
                    names=sorted(set(client.models()),key=str.casefold)
                self.events.put(('model_list',(request,names,None)))
            except Exception as exc:self.events.put(('model_list',(request,[],str(exc))))
        threading.Thread(target=fetch,daemon=True).start()
    def save_model_selection(self):
        name=self.model.currentText()
        if name:
            self.settings['model']=name
            try:save_assistant_settings(self.settings)
            except Exception as exc:self.statusBar().showMessage(f'Could not save model selection: {exc}')
    def apply_model_list(self, value):
        request,names,error=value
        if request!=self.model_request:return
        previous=self.model.currentText() or self.settings['model']
        self.model.clear();self.model.addItems(names)
        if previous in names:self.model.setCurrentText(previous)
        self.model.setEnabled(bool(names));self.refresh_models_button.setEnabled(True)
        if error:self.model_status.setText(f'Ollama unavailable: {error}. Check its URL in Settings, then refresh.')
        elif not names:self.model_status.setText('No installed models found on this Ollama server.')
        else:self.model_status.setText(f'{len(names)} installed models available')

    def send_chat(self):
        text=self.message.text().strip()
        if not text or self.busy() or self.disconnect_pending:return
        if not self.model.currentText():
            self.statusBar().showMessage('No model selected. Refresh models after checking your Ollama server.');return
        self.live_timer.stop()
        physical=self.controller if self.controller.connected else None
        if physical and not physical.connected:self.statusBar().showMessage('Connect the hand first.');return
        try:
            self.settings['model']=self.model.currentText().strip()
            if physical:physical.resume_motion()
            self.engine.start('text',self.settings,self.config,text,physical)
            self.transcript.append('You: '+text);self.message.clear()
        except Exception as exc:self.statusBar().showMessage(str(exc))
    def build_gestures(self):
        page=W.QWidget();v=W.QVBoxLayout(page)
        quick=W.QGroupBox('Quick gestures');grid=W.QGridLayout(quick)
        for index,(title,method) in enumerate(GESTURES.items()):
            grid.addWidget(button(title,lambda _,m=method:self.run_gesture(m)),index//5,index%5)
        v.addWidget(quick)
        v.addWidget(W.QLabel('Sequence · choose gestures or saved poses, then set the delay after each step.'))
        self.sequence_table=W.QTableWidget(0,2)
        self.sequence_table.setHorizontalHeaderLabels(['Gesture / saved pose','Delay after (s)'])
        self.sequence_table.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.Stretch)
        self.sequence_table.horizontalHeader().setSectionResizeMode(1,W.QHeaderView.ResizeToContents)
        self.sequence_table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.sequence_table.setSelectionMode(W.QAbstractItemView.SingleSelection)
        v.addWidget(self.sequence_table,1)
        row=W.QHBoxLayout()
        for text,callback in [('Add step',self.add_sequence_step),('Remove',self.remove_sequence_step),
                              ('Move up',lambda:self.reorder_sequence(-1)),('Move down',lambda:self.reorder_sequence(1))]:
            row.addWidget(button(text,callback))
        row.addStretch();v.addLayout(row)
        row=W.QHBoxLayout();self.sequence_loop=W.QCheckBox('Loop sequence');row.addWidget(self.sequence_loop)
        self.sequence_button=button('Run sequence',self.toggle_sequence,True);row.addWidget(self.sequence_button)
        row.addStretch();v.addLayout(row)
        note=W.QLabel('Disconnected: preview only. Connected: moves the hand. Delays start after commands are sent; the final delay is used between loops. Loop repeats until stopped.')
        note.setWordWrap(True);v.addWidget(note)
        self.tabs.addTab(page,'Gestures')
        self.tabs.currentChanged.connect(lambda _:self.refresh_sequence_choices())
    def sequence_choices(self):
        return [(name,('gesture',name)) for name in GESTURES]+[(f'Pose · {name}',('pose',name)) for name in sorted(self.config.poses)]
    def refresh_sequence_choices(self):
        if not hasattr(self,'sequence_table'):return
        for row in range(self.sequence_table.rowCount()):
            box=self.sequence_table.cellWidget(row,0);selected=box.currentData()
            box.clear()
            for label,data in self.sequence_choices():box.addItem(label,data)
            index=box.findData(selected)
            if selected and index<0:
                box.addItem('Missing · '+selected[1],selected);index=box.count()-1
            if index>=0:box.setCurrentIndex(index)
    def add_sequence_step(self, checked=False, step=None):
        row=self.sequence_table.rowCount();self.sequence_table.insertRow(row)
        choice=W.QComboBox()
        for label,data in self.sequence_choices():choice.addItem(label,data)
        delay=W.QDoubleSpinBox();delay.setRange(0,3600);delay.setDecimals(2);delay.setSingleStep(.1);delay.setValue(1)
        if step:
            key=(step['kind'],step['name']);index=choice.findData(key)
            if index<0:choice.addItem('Missing · '+step['name'],key);index=choice.count()-1
            choice.setCurrentIndex(index);delay.setValue(step['delay'])
        self.sequence_table.setCellWidget(row,0,choice);self.sequence_table.setCellWidget(row,1,delay)
        self.sequence_table.setRowHeight(row,max(choice.sizeHint().height(),delay.sizeHint().height())+6)
        self.sequence_table.selectRow(row)
    def sequence_steps(self):
        steps=[]
        for row in range(self.sequence_table.rowCount()):
            kind,name=self.sequence_table.cellWidget(row,0).currentData()
            steps.append(dict(kind=kind,name=name,delay=self.sequence_table.cellWidget(row,1).value()))
        return steps
    def remove_sequence_step(self):
        row=self.sequence_table.currentRow()
        if row>=0:self.sequence_table.removeRow(row)
    def reorder_sequence(self, direction):
        row=self.sequence_table.currentRow();target=row+direction;steps=self.sequence_steps()
        if row<0 or not 0<=target<len(steps):return
        steps[row],steps[target]=steps[target],steps[row]
        self.sequence_table.setRowCount(0)
        for step in steps:self.add_sequence_step(step=step)
        self.sequence_table.selectRow(target)
    @staticmethod
    def danger_style(widget, enabled):
        if bool(widget.property('danger'))!=enabled:
            widget.setProperty('danger',enabled)
            widget.style().unpolish(widget);widget.style().polish(widget);widget.update()
    def toggle_sequence(self):
        if self.sequence_running:self.stop()
        else:self.run_sequence()
    def sync_sequence_button(self):
        stopping=self.sequence_running and self.sequence_cancel.is_set()
        self.sequence_button.setText('Stopping…' if stopping else ('Stop sequence' if self.sequence_running else 'Run sequence'))
        self.sequence_button.setEnabled(not stopping)
        self.danger_style(self.sequence_button,self.sequence_running)

    def run_sequence(self):
        if self.busy() or self.disconnect_pending:return
        try:steps=prepare_steps(self.sequence_steps(),self.config)
        except Exception as exc:self.statusBar().showMessage(str(exc));return
        self.live_timer.stop();self.sequence_cancel=threading.Event()
        physical=self.controller if self.controller.connected else None
        if physical:physical.resume_motion()
        config=deepcopy(self.config);cancel=self.sequence_cancel;loop=self.sequence_loop.isChecked()
        self.sequence_running=True;self.sync_sequence_button()
        def execute():
            try:execute_steps(steps,config,physical,cancel,lambda kind,value:self.events.put((kind,value)),loop=loop)
            finally:self.events.put(('sequence_done',None))
        self.work('Sequence',execute)
    def run_gesture(self,method):
        if not self.controller.connected:self.statusBar().showMessage('Connect the hand first.');return
        if self.busy():return
        self.controller.resume_motion()
        def run():self.gestures.torque_on();getattr(self.gestures,method)()
        self.work('Gesture',run)
    def open_about(self):
        if getattr(self,'about_dialog',None) is not None:
            self.about_dialog.show();self.about_dialog.raise_();self.about_dialog.activateWindow();return
        dialog=W.QDialog(self);self.about_dialog=dialog
        dialog.setWindowTitle('About AmazingHand Studio');dialog.setWindowFlag(C.Qt.WindowContextHelpButtonHint,False)
        layout=W.QVBoxLayout(dialog)
        title=W.QLabel('AmazingHand Studio');title.setStyleSheet('font-size:16pt;font-weight:600');layout.addWidget(title)
        text=W.QTextBrowser();text.setOpenExternalLinks(True)
        text.setHtml("""<p>Pose, gesture, and LLM control for AmazingHand.</p>
        <p><b>Developed by</b><br>
        Immanuel Jose Valencia · Jeremy Laville · Ryan Rhay Vicerra · Aaron Raymond See</p>
        <p><b>Institutions</b><br>
        Pollen Robotics SAS, 2 Place Jean Jaurès, 33000 Bordeaux, France<br>
        Intelligent Systems Laboratory, De La Salle University<br>
        Department of Biomedical, Manufacturing and Robotics Engineering, De La Salle University, Philippines<br>
        Tactile Optimization and User-Centric Haptics (TOUCH) Lab, National Chin Yi University of Technology, Taiwan</p>
        <p><small>© 2026 AmazingHand Studio contributors.</small></p>
        <p><small><b>Third-party notices</b><br>
        Hand models: Pollen Robotics and AmazingHand contributors.
        Upstream project: Apache 2.0; mechanical design: CC BY 4.0.<br>
        MuJoCo/Mink demo: Steve N'Guyen. Bundled model files are unmodified.</small></p>""")
        layout.addWidget(text,1)
        row=W.QHBoxLayout()
        row.addStretch();row.addWidget(button('Close',dialog.close));layout.addLayout(row)
        # Measure rich text at the current font/DPI, bounded by the active screen.
        document=text.document().clone()
        document.setTextWidth(-1)
        margins=layout.contentsMargins()
        chrome=margins.left()+margins.right()+2*text.frameWidth()+24
        available=self.screen().availableGeometry()
        width=min(int(document.idealWidth())+chrome, int(available.width()*0.9))
        document.setTextWidth(max(1,width-chrome))
        height=int(document.size().height())+title.sizeHint().height()+row.sizeHint().height()+margins.top()+margins.bottom()+2*layout.spacing()+24
        dialog.resize(width,min(height,int(available.height()*0.9)))
        dialog.show()

    def open_settings(self):
        if self.busy():self.statusBar().showMessage('Finish the current action before changing settings.');return
        dialog=W.QDialog(self);dialog.setWindowTitle('Settings and calibration');dialog.resize(650,650);outer=W.QVBoxLayout(dialog)
        page=W.QWidget();form=W.QFormLayout(page)
        port=W.QComboBox();port.setEditable(True);port.addItems(list_serial_ports());port.setCurrentText(self.config.serial.port)
        side=W.QComboBox();side.addItems(['left','right']);side.setCurrentText(self.config.side)
        baud=W.QLabel('1,000,000 baud (fixed)')
        speed=W.QSpinBox();speed.setRange(1,7);speed.setValue(self.config.demo.max_speed)
        url=W.QLineEdit(self.settings['ollama_url'])
        for label,widget in [('Port',port),('Hand',side),('Baud rate',baud),('Movement speed',speed),('Ollama URL',url)]:form.addRow(label,widget)
        fields={}
        for name in FINGER_ORDER:
            finger=self.config.fingers[name];row=W.QHBoxLayout();values=[]
            for label,value,low,high in [('ID 1',finger.servos[0],1,253),('ID 2',finger.servos[1],1,253),('Middle 1',finger.middle[0],-150,150),('Middle 2',finger.middle[1],-150,150)]:
                col=W.QVBoxLayout();col.addWidget(W.QLabel(label));spin=W.QDoubleSpinBox();spin.setDecimals(0 if label.startswith('ID') else 1);spin.setRange(low,high);spin.setValue(value);col.addWidget(spin);row.addLayout(col);values.append(spin)
            form.addRow(name.title(),row);fields[name]=values
        outer.addWidget(scroll(page));buttons=W.QDialogButtonBox(W.QDialogButtonBox.Save|W.QDialogButtonBox.Cancel);outer.addWidget(buttons);buttons.rejected.connect(dialog.reject)
        def apply():
            try:
                config=deepcopy(self.config);config.serial.port=port.currentText().strip();config.serial.baudrate=SERIAL_BAUDRATE;config.side=side.currentText();config.demo.max_speed=speed.value()
                for name,values in fields.items():config.fingers[name].servos=[int(w.value()) for w in values[:2]];config.fingers[name].middle=[w.value() for w in values[2:]]
                ids=[i for f in config.fingers.values() for i in f.servos]
                if len(set(ids))!=8:raise ValueError('Servo IDs must be unique.')
                settings=dict(self.settings,ollama_url=url.text().strip(),model=self.model.currentText().strip() or self.settings['model'])
                save_assistant_settings(settings);save_config(config,self.path);self.controller.set_config(config)
                self.config=config;self.settings=settings;self.preview.request();self.refresh_models();dialog.accept()
            except Exception as exc:W.QMessageBox.warning(dialog,'Settings',str(exc))
        buttons.accepted.connect(apply);dialog.exec_()
    def drain(self):
        while True:
            try:kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='model_list':self.apply_model_list(value)
            elif kind=='servo':
                sid,angle=value
                for name,finger in self.config.fingers.items():
                    if sid in finger.servos:self.pose[name][finger.servos.index(sid)]=angle-finger.middle[finger.servos.index(sid)]
                self.preview.request()
            elif kind=='answer':self.transcript.append('Assistant: '+value.text);self.draft.clear()
            elif kind=='draft':self.draft.setText(str(value))
            elif kind in ('status','error'):self.statusBar().showMessage(str(value))
            elif kind=='connection_error':
                self.statusBar().showMessage('Connection failed: '+str(value))
                self.connection_pending=False
                W.QMessageBox.warning(self,'Could not connect',str(value))
            elif kind=='sequence_done':
                self.sequence_running=False;self.sync_sequence_button()
            elif kind=='hardware_done':self.connection_pending=False
            elif kind=='done':self.draft.clear()
            elif kind=='release':
                if self.busy():C.QTimer.singleShot(100,lambda:self.events.put(('release',None)))
                elif self.controller.connected:self.work('Release hand',lambda:self.controller.write_torque(None,TORQUE_FREE))
        self.connection.setText('Connecting…' if self.connection_pending else (('Connected · '+self.config.serial.port) if self.controller.connected else 'Disconnected'))
        self.danger_style(self.connect_button,self.controller.connected)
        self.connect_button.setText('Connecting…' if self.connection_pending else ('Disconnect' if self.controller.connected else 'Connect'))
        self.connect_button.setEnabled((self.controller.connected or not self.busy()) and not self.disconnect_pending and not self.connection_pending)
        if self.disconnect_pending and not self.busy():
            self.disconnect_pending=False
            self.work('Disconnect',self.controller.disconnect)
        if self.closing and not self.busy():self.close()
    def closeEvent(self,event):
        self.closing=True;self.stop(release=False);self.engine.warm_cancel.set()
        if self.busy():event.ignore();return
        self.controller.disconnect();self.poll.stop();self.preview.shutdown();event.accept()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config');args=parser.parse_args()
    run_app(args.config)

def run_app(config_path=None):
    W.QApplication.setAttribute(C.Qt.AA_EnableHighDpiScaling,True)
    W.QApplication.setAttribute(C.Qt.AA_UseHighDpiPixmaps,True)
    app=W.QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    window=Studio(config_path);window.show();app.exec_()

if __name__=='__main__':main()
