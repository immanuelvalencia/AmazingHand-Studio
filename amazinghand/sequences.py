"""Ordered gesture/pose execution with cancellable inter-step delays."""
from copy import deepcopy
import math
from .assistant_engine import CommandController
from .gestures import AmazingHandGestures
from .chat.cancellation import Cancelled

GESTURES = {'Open':'open_hand','Close':'close_hand','Spread':'spread_hand','Clench':'clench_hand',
            'Point':'index_pointing','Victory':'victory','Pinched':'pinched','No':'no_no','OK':'perfect'}

def prepare_steps(steps, config):
    if not steps:raise ValueError('Add at least one sequence step.')
    result=[]
    for step in steps:
        kind,name,delay=step['kind'],step['name'],float(step['delay'])
        if not math.isfinite(delay) or not 0<=delay<=3600:raise ValueError('Delay must be between 0 and 3600 seconds.')
        if kind=='gesture' and name in GESTURES:pose=None
        elif kind=='pose' and name in config.poses:pose=deepcopy(config.poses[name])
        else:raise ValueError(f'Sequence action is unavailable: {name}')
        result.append(dict(kind=kind,name=name,delay=delay,pose=pose))
    return result

def execute_steps(steps, config, controller, cancel, emit, loop=False):
    adapter=CommandController(config,cancel,emit,controller)
    gestures=AmazingHandGestures(adapter)
    try:
        cycle=1
        while True:
            for index,step in enumerate(steps):
                adapter.check()
                prefix=f'Loop {cycle} · ' if loop else ''
                emit('status',f'{prefix}Step {index+1}/{len(steps)} · {step["name"]}')
                gestures.torque_on()
                if step['kind']=='pose':gestures.move_pose(step['pose'])
                else:getattr(gestures,GESTURES[step['name']])()
                adapter.check()
                if (loop or index<len(steps)-1) and cancel.wait(step['delay']):raise Cancelled()
            if not loop:break
            cycle+=1
        emit('status','Sequence complete')
    except Cancelled:
        emit('status','Sequence stopped')
