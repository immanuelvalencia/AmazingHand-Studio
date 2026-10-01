"""Ordered, off-screen kinematics snapshots from actual controller targets."""
from copy import deepcopy
import json
import re
from pathlib import Path

from .kinematics import AmazingHandKinematics


def slug(value):
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")[:48] or "none"


class TrialCaptures:
    def __init__(self, directory, config, models):
        self.root = Path(directory) / "captures"
        self.config = config
        self.groups = {("deterministic", ""): "01_rules-only"}
        for model in dict.fromkeys(models):
            for algorithm in ("llm", "hybrid"):
                self.groups[(algorithm, model)] = f"{len(self.groups)+1:02}_{slug(model)}_{algorithm}"
        self.root.mkdir(exist_ok=True)
        (self.root / "README.md").write_text(
            "# Kinematics and MuJoCo captures\n\nFolders: numbered model/method, repetition, then trial order and command. "
            "Each numbered state has a _kinematics.png and _mujoco.png pair from the same solved configuration. "
            "000 is the initial commanded state; subsequent PNGs follow executed plan order. "
            "No gestures include intermediate paired-motor target phases. These are discrete commanded configurations, "
            "not physical camera images or continuous-motion measurements. Rejected/error trials retain only the initial "
            "state when no action was dispatched. Each trial has a capture manifest with full labels and offsets.\n",
            encoding="utf-8")

    def render(self, pose, path, title):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        model = AmazingHandKinematics(self.config)
        points, bones, labels = model.points_for_pose(pose)
        fig = Figure(figsize=(6, 6), dpi=120, facecolor="white")
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111, projection="3d")
        for start, end, color, width in bones:
            a, b = points[start]*1000, points[end]*1000
            ax.plot([a[0], b[0]], [a[2], b[2]], [a[1], b[1]], color=color, linewidth=width/2)
        for name, point in labels.items():
            p = points[point]*1000
            ax.text(p[0], p[2], p[1]+4, name, fontsize=10)
        ax.set(xlim=(-110,110), ylim=(-110,110), zlim=(-80,160),
               xlabel="Lateral (mm)", ylabel="Depth (mm)", zlabel="Height (mm)")
        ax.set_box_aspect((220,220,240)); ax.view_init(elev=20, azim=-65)
        fig.suptitle(title, fontsize=10, wrap=True)
        fig.text(.5,.025,"Commanded-target kinematics · not measured physical pose",ha="center",fontsize=8)
        fig.savefig(path)
        fig.clear()
        # Reuse this exact solved model/data state for the solid mesh render.
        solid_path=path.with_name(path.stem.removesuffix("_kinematics")+"_mujoco.png")
        self.render_mujoco(model,solid_path,title)
        return {"kinematics":path.name,"mujoco":solid_path.name,
                "scene":str(model.scene_path),"qpos":model.data.qpos.tolist()}

    def render_mujoco(self, model, path, title):
        from PIL import Image, ImageDraw, ImageFont
        mj=model.mujoco
        model.model.vis.global_.offwidth=720
        model.model.vis.global_.offheight=720
        camera=mj.MjvCamera()
        mj.mjv_defaultCamera(camera)
        camera.lookat[:]=[0,0,0.09]
        camera.distance=0.38
        camera.azimuth=-45 if self.config.side=="right" else 135
        camera.elevation=-20
        # Hide IK target markers in this private rendering model; retain hand meshes.
        for geom in range(model.model.ngeom):
            if model.model.body_mocapid[model.model.geom_bodyid[geom]] >= 0:
                model.model.geom_rgba[geom,3]=0
        with mj.Renderer(model.model,height=720,width=720) as renderer:
            renderer.update_scene(model.data,camera=camera)
            pixels=renderer.render().copy()
        canvas=Image.new("RGB",(720,820),"white")
        canvas.paste(Image.fromarray(pixels),(0,70))
        draw=ImageDraw.Draw(canvas)
        font_path=Path("C:/Windows/Fonts/segoeui.ttf")
        font=ImageFont.truetype(str(font_path),17) if font_path.exists() else ImageFont.load_default()
        draw.multiline_text((16,10),title,fill="#0f172a",font=font)
        draw.text((16,792),"MuJoCo mesh render · commanded configuration",fill="#475569",font=font)
        canvas.save(path)

    def save(self, case, row, evaluation, initial):
        group = self.groups[(row["algorithm"], row.get("model", ""))]
        folder = self.root / group / f"repetition_{row.get('repetition',1):02}" / f"{row.get('order',0):04}_{slug(case['id'])}_{slug(case['command'])}"
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"trial_id":row["trial_id"],"case_id":case["id"],"command":case["command"],
                    "model":row.get("model",""),"algorithm":row["algorithm"],"outcome":row["outcome"],
                    "backend":"kinematics","capture_views":["kinematics","mujoco"],"frames":[],"status":"writing"}
        pose = deepcopy(initial)
        def frame(label, offsets):
            filename=f"{len(manifest['frames']):03}_{label}_kinematics.png"
            item={"label":label,"offsets":deepcopy(offsets),"status":"writing"}
            manifest["frames"].append(item)
            try:
                item.update(self.render(offsets,folder/filename,f"{case['id']} | {row['algorithm']} | {row.get('model') or 'rules'}\n{label}"))
                item["status"]="complete"
            except Exception as exc:
                item.update(status="error",error=str(exc))
                raise
        try:
            frame("initial",pose)
            for i, actual in enumerate(evaluation.get("actual_frames",[]),1):
                plan=row.get("plan") or []
                action=plan[i-1]["action"] if i<=len(plan) else "action"
                if action=="no":
                    pending=set(); phase=0; index_servos=set(self.config.fingers["index"].servos)
                    for servo,degrees in actual["writes"]:
                        for name,finger in self.config.fingers.items():
                            if servo in finger.servos:
                                j=finger.servos.index(servo); pose[name][j]=degrees-finger.middle[j]
                        if servo in index_servos: pending.add(servo)
                        if pending==index_servos:
                            phase+=1; frame(f"step_{i:02}_no_phase_{phase:02}",pose);pending.clear()
                pose=deepcopy(actual["offsets"])
                frame(f"step_{i:02}_{slug(action)}",pose)
            manifest["status"]="complete"
        except Exception as exc:
            manifest.update(status="error",error=str(exc))
        finally:
            (folder/"capture_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
            record={"trial_id":row["trial_id"],"folder":folder.relative_to(self.root.parent).as_posix(),
                    "frame_count":sum(f.get('status')=='complete' for f in manifest["frames"]),
                    "image_count":len(list(folder.glob('*.png'))),"status":manifest["status"]}
            if "error" in manifest: record["error"]=manifest["error"]
            with (self.root/"index.jsonl").open("a",encoding="utf-8") as out: out.write(json.dumps(record)+"\n")
        return record
