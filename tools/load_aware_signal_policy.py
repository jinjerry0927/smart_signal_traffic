"""Capacity-aware correction: preserve the frozen V2 and shared neural scorer."""
from types import MappingProxyType
import numpy as np
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
from transferable_signal.controllers import legal_best,webster_greens

VARIANTS=MappingProxyType({f'load_aware{t}':MappingProxyType(dict(version='load-aware-rules-v4',
    guard_floor_s=t,queue_margin=.01,pressure_margin=2,low_hold_until_s=25,low_red_limit=.3)) for t in (25,30)})

class LoadAwareSignalPolicy:
    def __init__(self,path,variant):
        if variant not in VARIANTS: raise ValueError('Unknown load-aware variant')
        self.base=AdaptiveSmartSignalV2(path,'load_gate_pressure_wait35')
        self.data,self.variant,self.config=self.base.data,variant,VARIANTS[variant]
        self.last_decision=None

    def action(self,jid,observation,clock):
        action=self.base.action(jid,observation,clock)
        f=np.asarray(observation['features'],dtype=float)
        pressure=np.asarray(observation['pressure'],dtype=float)
        flow=np.asarray(observation['critical_ratio'],dtype=float)
        mask=np.asarray(observation['mask'],dtype=bool).copy()
        if flow.shape!=(len(f),) or not np.isfinite(flow).all() or (flow<0).any():
            raise ValueError('Invalid measured critical ratios')
        if not np.array_equal(mask,clock.mask()): raise ValueError('Safety clock mask differs')
        current,cfg=clock.current,self.config
        other=mask.copy(); other[current]=False
        details=dict(self.base.last_decision)
        mode,held,guard='v2',False,False
        # Hold a small stream of approaching traffic briefly, never an empty
        # approach and never ahead of a substantially older queued approach.
        held=bool(details['low_load'] and clock.stage=='green' and mask[current]
            and clock.elapsed<cfg['low_hold_until_s'] and f[current,1]>0 and other.any()
            and np.max(pressure[other])<=pressure[current]+cfg['pressure_margin']
            and not np.any(other & (f[:,0]>0) & (f[:,4]>=cfg['low_red_limit'])))
        if held: action,mode=current,'approaching_traffic_hold'
        estimate=webster_greens(flow.tolist(),clock.limits)
        threshold=max(cfg['guard_floor_s'],estimate[current])
        # Do not override learned holding merely because storage-normalized
        # queues differ: the alternative must also have higher positive pressure.
        candidates=other & (f[:,0]>=f[current,0]+cfg['queue_margin']) \
            & (pressure>max(0.,pressure[current])+cfg['pressure_margin'])
        if clock.stage=='green' and clock.elapsed>=threshold and action==current and candidates.any():
            action,mode,guard=legal_best(pressure,candidates.copy()),'capacity_pressure_release',True
        if not mask[action]: raise ValueError('Illegal load-aware choice')
        self.last_decision=dict(details,version=cfg['version'],variant=self.variant,v2_action=details['action'],
            action=int(action),load_aware_mode=mode,approaching_hold=held,capacity_guard=guard,
            estimated_greens_s=estimate,guard_threshold_s=threshold,load_candidates=candidates.tolist())
        return int(action)
