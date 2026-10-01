"""Qt viewports backed by the bundled MuJoCo hand models."""
import numpy as np
from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W
from .kinematics import AmazingHandKinematics

class Viewport(W.QLabel):
    changed = C.pyqtSignal()
    def __init__(self, preview):
        super().__init__()
        self.preview = preview
        self.setMinimumSize(100, 140)
        self.setSizePolicy(W.QSizePolicy.Ignored, W.QSizePolicy.Ignored)
        self.setAlignment(C.Qt.AlignCenter)
        self.setStyleSheet("background:#172b40; color:white; border-radius:6px")
        self.drag = None
    def resizeEvent(self, event):
        self.preview.request()
        super().resizeEvent(event)
    def mousePressEvent(self, event): self.drag = event.pos()
    def mouseMoveEvent(self, event):
        if self.drag is not None:
            delta = event.pos()-self.drag
            self.preview.azimuth -= delta.x()*.4
            self.preview.elevation = max(-89,min(89,self.preview.elevation+delta.y()*.4))
            self.drag = event.pos(); self.preview.request()
    def mouseReleaseEvent(self, event): self.drag = None
    def wheelEvent(self, event):
        self.preview.distance = max(.08,min(2,self.preview.distance*(.9 if event.angleDelta().y()>0 else 1.1)))
        self.preview.request()

class Preview(W.QWidget):
    def __init__(self, app):
        super().__init__(); self.app=app; self.hand=None; self.renderers={}; self.last_error=None
        self.azimuth=135; self.elevation=-20; self.distance=.4
        self.timer=C.QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.render)
        layout=W.QVBoxLayout(self)
        bar=W.QHBoxLayout();self.mode=W.QComboBox();self.mode.addItems(['3D model','Kinematics','Both'])
        reset=W.QPushButton('Reset view');reset.clicked.connect(self.reset)
        bar.addWidget(self.mode,1);bar.addWidget(reset);layout.addLayout(bar)
        self.split=W.QSplitter(C.Qt.Horizontal);layout.addWidget(self.split,1);self.views={}
        for name in ('3D model','Kinematics'):
            panel=W.QWidget();column=W.QVBoxLayout(panel);column.setContentsMargins(0,0,0,0)
            column.addWidget(W.QLabel(name));canvas=Viewport(self);column.addWidget(canvas,1)
            self.split.addWidget(panel);self.views[name]=(panel,canvas)
        self.caption=W.QLabel();self.caption.setWordWrap(True);layout.addWidget(self.caption)
        self.mode.currentTextChanged.connect(self.layout_views);self.layout_views()
    def layout_views(self):
        for name,(panel,canvas) in self.views.items(): panel.setVisible(self.mode.currentText() in (name,'Both'))
        self.split.setSizes([1,1]);self.request()
    def reset(self): self.azimuth=135;self.elevation=-20;self.distance=.4;self.request()
    def request(self): self.timer.start(35)
    @staticmethod
    def close_renderer(renderer):
        context=getattr(renderer,'_gl_context',None)
        if context is not None: context.make_current()
        renderer.close()
    def shutdown(self):
        self.timer.stop()
        for renderer in self.renderers.values():self.close_renderer(renderer)
        self.renderers.clear()
    def render(self):
        if not self.isVisible():return
        try:
            if self.hand is None or self.hand._side != self.app.config.side:
                self.shutdown();self.hand=AmazingHandKinematics(self.app.config)
                self.azimuth=135;self.elevation=-20;self.distance=.4
            self.hand.update_config(self.app.config)
            points,bones,_=self.hand.points_for_pose(self.app.pose)
            hand=self.hand; mj=hand.mujoco
            camera=mj.MjvCamera();mj.mjv_defaultCamera(camera)
            camera.lookat[:]=[0,0,.09];camera.distance=self.distance
            camera.azimuth=self.azimuth;camera.elevation=self.elevation
            for name,(panel,canvas) in self.views.items():
                if panel.isHidden():continue
                scale=min(canvas.devicePixelRatioF(),1024/max(32,canvas.width(),canvas.height()))
                width,height=max(32,int(canvas.width()*scale)),max(32,int(canvas.height()*scale))
                renderer=self.renderers.get(name)
                if renderer and (renderer.width,renderer.height)!=(width,height):
                    self.close_renderer(renderer);del self.renderers[name];renderer=None
                if renderer is None:
                    hand.model.vis.global_.offwidth=1024;hand.model.vis.global_.offheight=1024
                    renderer=mj.Renderer(hand.model,height=height,width=width);self.renderers[name]=renderer
                camera.distance=self.distance*max(1.,.65*height/width)
                renderer.update_scene(hand.data,camera=camera)
                targets={hand.model.body(f'finger{i}_target').id for i in range(1,5)}
                for geom in renderer.scene.geoms[:renderer.scene.ngeom]:
                    if geom.objtype==mj.mjtObj.mjOBJ_GEOM and geom.objid>=0 and hand.model.geom_bodyid[geom.objid] in targets:geom.rgba[3]=0
                if name=='Kinematics':self._kinematic_scene(renderer,hand,points,bones)
                pixels=renderer.render();image=G.QImage(pixels.data,width,height,pixels.strides[0],G.QImage.Format_RGB888).copy()
                canvas.setPixmap(G.QPixmap.fromImage(image).scaled(canvas.size(),C.Qt.KeepAspectRatio,C.Qt.SmoothTransformation))
            self.caption.setText(f'{self.app.config.side.title()} hand · commanded pose\nDrag to orbit · scroll to zoom')
            self.last_error=None
        except Exception as exc:
            self.last_error=str(exc);self.caption.setText(f'Preview unavailable: {exc}')

    def _kinematic_scene(self, renderer, hand, points, bones):
        scene = renderer.scene
        scene.ngeom = 0
        origin = np.array([0., 0., .080])
        world = {name: point[[2, 0, 1]] + origin for name, point in points.items()}
        identity = np.eye(3).ravel()
        mj = hand.mujoco
        for start, end, color, weight in bones:
            geom = scene.geoms[scene.ngeom]
            rgba = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)] + [1.]
            mj.mjv_initGeom(geom, mj.mjtGeom.mjGEOM_CAPSULE, np.zeros(3),
                           np.zeros(3), identity, np.array(rgba, dtype=np.float32))
            mj.mjv_connector(geom, mj.mjtGeom.mjGEOM_CAPSULE, weight * .0004,
                             world[start], world[end])
            scene.ngeom += 1
        for point in world.values():
            geom = scene.geoms[scene.ngeom]
            mj.mjv_initGeom(geom, mj.mjtGeom.mjGEOM_SPHERE, np.full(3, .0035),
                           point, identity, np.array([.95, .6, .1, 1.], dtype=np.float32))
            scene.ngeom += 1
