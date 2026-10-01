"""Prospective wording checks specified after the output-contract revision was frozen."""
from copy import deepcopy
from pathlib import Path
import json


def cases():
    rows=[]
    initial={'index':13,'middle':39,'ring':58,'thumb':83}
    def add(command,steps,category='finger',**context):
        rows.append({'id':f'IR{len(rows)+1:03}','command':command,'category':category,
                     'expected':{'steps':steps},'suite_version':'inference-regression-40-v1',**deepcopy(context)})
    for finger,value in zip(initial,(43,56,69,82)):
        add(f'I would like only {finger} at {value}% opening. Preserve every other finger.',
            [{'action':'pose','fingers':{finger:value}}],targets=initial)
    for finger,word,delta,value in [('index','increase',11,24),('middle','reduce',5,34),
                                    ('ring','increase',19,77),('thumb','reduce',23,60)]:
        add(f'From the commanded position, {word} the {finger} opening by {delta} percentage points. Nothing else should change.',
            [{'action':'pose','fingers':{finger:value}}],targets=initial)
    for finger in initial:
        add('The same finger should finish at 62 percent opening; keep the others unchanged.',
            [{'action':'pose','fingers':{finger:62}}],targets=initial,
            previous={'steps':[{'action':'pose','fingers':{finger:initial[finger]}}]})
    for finger in initial:
        add(f'For {finger} only: start at 29% opening, then increase it by 12 percentage points, then finish at 71%.',
            [{'action':'pose','fingers':{finger:v}} for v in (29,41,71)],'sequence',targets=initial)
    for action,command in [('peace','Please make the victory V gesture.'),('yes','Give the predefined thumbs-up signal.'),
                           ('no','Perform the predefined index-wag no signal once.'),('point','Use the pointing gesture for 0.9 seconds.')]:
        add(command,[{'action':action,**({'hold_seconds':.9} if action=='point' else {})}],'pose')
    for command,result in [('Calculate the product of nine minus three and two.',12),
                            ('Divide fifteen by three and show the result.',5),
                            ('Display the sum of eight and eleven.',19),
                            ('Show the result of subtracting four from thirteen.',9)]:
        add(command,[{'action':'number','number':result}],'arithmetic')
    for a,b in [('index','ring'),('middle','thumb'),('index','thumb'),('middle','ring')]:
        add(f'In a single pose put {a} at 18% opening and {b} at 81%; leave the remaining two unchanged.',
            [{'action':'pose','fingers':{a:18,b:81}}],'sequence',targets=initial)
    for command in ['Move the little finger to 40% opening.', 'Please bend the wrist by 12 degrees.',
                    'Make the index opening 104 percent.', 'Give thumb an opening of negative six percent.',
                    'Point using speed 9.', 'Hold the closed hand for seven seconds.',
                    'First open the hand, then set ring to 106 percent.',
                    'Set middle to 45 percent and thumb to 107 percent simultaneously.',
                    'Make that finger more open.', 'Perform the previous command again.',
                    'Close index by 40 percentage points.', 'Increase thumb opening by 30 percentage points.']:
        add(command,[{'action':'unknown'}],'robustness',**({'targets':initial} if command.startswith(('Close index','Increase thumb')) else {}))
    assert len(rows)==40
    return rows


if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'configs/experiments/development/inference_regression_40.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise RuntimeError('Keep the frozen set; use a new version for changes.')
    target.write_text(json.dumps(cases(),indent=2),encoding='utf-8')
    print(target)
