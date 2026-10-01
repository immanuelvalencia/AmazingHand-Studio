"""Render all distinct frozen configurations for researcher review; no hardware."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .config import HandConfig
from .experiment_capture import TrialCaptures
from .experiments import ROOT, digest


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,help='Folder containing commands, protocol and frozen references')
    args=parser.parse_args()
    root=args.study or ROOT/'configs/experiments/validation'
    frozen=json.loads((root/'validation_references.json').read_text(encoding='utf-8'))
    protocol_path=(root if args.study else root.parent)/'study_protocol.json'
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    folder=root/'pose_gallery';folder.mkdir(exist_ok=True)
    renderer=TrialCaptures(folder,HandConfig.from_dict(protocol['hand_config']),[])
    poses={}
    def add(pose,origin):
        key=digest(pose)
        if key not in poses:poses[key]={'offsets':pose,'origins':[]}
        poses[key]['origins'].append(origin)
    for key,case in frozen['cases'].items():
        add(case['initial_offsets'],key+' initial')
        for i,frame in enumerate(case['frames'],1):add(frame['offsets'],f'{key} step {i}')
        for step,frames in case['dynamic_frames'].items():
            for phase,frame in enumerate(frames,1):add(frame['offsets'],f'{key} step {int(step)+1} phase {phase}')
    for key,case in ({} if args.study else frozen['controller_references']).items():
        for i,frame in enumerate(case['frames'],1):add(frame['offsets'],f'{key} reference {i}')
    font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',15)
    pages=[];manifest=[]
    for i,item in enumerate(poses.values()):
        if i%16==0:pages.append(Image.new('RGB',(1000,1200),'white'))
        name=f'{i+1:03}_pose'
        files=renderer.render(item['offsets'],folder/f'{name}_kinematics.png',f"Pose {i+1:03} | {item['origins'][0]}")
        with Image.open(folder/files['mujoco']) as source:
            source.thumbnail((245,270));tile=source.copy()
        x=(i%4)*250;y=((i%16)//4)*300
        pages[-1].paste(tile,(x,y))
        ImageDraw.Draw(pages[-1]).text((x+8,y+275),f"{i+1:03}  {item['origins'][0]}",fill='black',font=font)
        manifest.append({'id':name,**item,**files})
    for i,page in enumerate(pages,1):page.save(folder/f'gallery_{i:02}.png')
    (folder/'index.json').write_text(json.dumps({'reference_sha256':frozen['reference_sha256'],'poses':manifest},indent=2),encoding='utf-8')
    print(f'Rendered {len(manifest)} distinct configurations on {len(pages)} gallery pages.')


if __name__=='__main__':main()
