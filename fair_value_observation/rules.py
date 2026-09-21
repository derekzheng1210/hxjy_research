"""Frozen quote-only adapters for the research candidate registry."""
import hashlib
import json
import numpy as np
from fair_value_research.selective import configs, masks_and_anchors
from fair_value_research.selective_followup import apply as persistent_apply
from fair_value_research.widening import GATES, apply as widening_apply
from fair_value_research.widening_events import apply as events_apply

VERSION='quote-only-v1'
GATE_NAMES={'all':'报价偏离','liquid_quote':'多日窄双边','trade_confirmed':'成交确认',
 'interval_confirmed':'区间外成交确认','signal_to_noise':'高信噪比','persistent':'持续偏离',
 'known_size':'已知挂量','trade_echo':'近期成交同向','large_evidence':'较大报价偏离',
 'persistent_offer':'持续卖价偏高','offer_only':'持续单边卖盘','rising_offer':'卖价上移',
 'rising_offer_only':'单边卖价上移','known_sell_size':'已知卖压','growing_sell_size':'卖量增长',
 'peer_confirmed':'同主体确认','trend_peer':'卖价上移及主体确认','no_trade_conflict':'无已知成交背离',
 'any':'近期卖价上移','bilateral':'窄双边卖价上移','single_offer':'单边卖价上移事件',
 'peer':'卖价上移及同主体','sell_size':'卖价上移及卖量增长','official_continuation':'卖价上移及官方延续'}


def registry():
    live={};archive=[]
    def add(engine,anchor,gate,scale,minimum,source):
        spec=dict(engine=engine,anchor=anchor,gate=gate,scale=float(scale),minimum=max(1.,float(minimum)))
        key=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:16]
        if key not in live:
            live[key]=dict(id=key,spec=spec,family=engine+':'+gate,
                name=f'{GATE_NAMES.get(gate,gate)} · {anchor} ×{scale:g}',sources=[])
        live[key]['sources'].append(source)
    for c in configs():
        if c['anchor'] in {'consensus','trade_center'}:
            archive.append(dict(source='selective:'+c['id'],reason='独立成交定价或报价成交均价，仅历史档案',spec=c));continue
        anchor='quote_level_only' if c['anchor'] in {'v1','levels_only'} else c['anchor']
        add('selective',anchor,c['gate'],c['scale'],c['minimum'],'selective:'+c['id'])
    for anchor in ['level','quote','boundary']:
        for gate in ['persistent','known_size','trade_echo','large_evidence']:
            for scale in [.25,.5]:add('persistent',anchor,gate,scale,1,f'persistent:{anchor}:{gate}:{scale}')
    for gate in GATES:
        for scale in [.25,.5,1.]:add('widening','quote_level_only',gate,scale,1,f'widening:{gate}:{scale}')
    for gate in ['any','bilateral','single_offer','peer','sell_size','official_continuation']:
        for scale in [.5,1.]:add('events','offer_slope',gate,scale,1,f'events:{gate}:{scale}')
    return list(live.values()),archive


def apply(frame,spec):
    engine=spec['engine']
    if engine=='selective':
        gates,anchors=masks_and_anchors(frame)
        anchors['quote_level_only']=frame.quote_level_only
        delta=(anchors[spec['anchor']]*spec['scale']).clip(-10,10)
        mask=gates[spec['gate']]&delta.abs().ge(spec['minimum'])
    elif engine=='persistent':
        f=frame.copy(deep=False);f=f.assign(level_delta=f.quote_level_only)
        delta,mask=persistent_apply(f,spec)
    elif engine=='widening':delta,mask=widening_apply(frame,spec)
    else:delta,mask=events_apply(frame,spec)
    # Failed trade API is not proof of "no conflicting trades".
    trade_gates={'trade_confirmed','interval_confirmed','signal_to_noise','trade_echo','no_trade_conflict'}
    if spec['gate'] in trade_gates and 'trade_status' in frame:
        mask=mask&~frame.trade_status.fillna('failed').str.contains('fail|error|unknown',case=False,regex=True)
    mask=mask&frame.quote_days.gt(0)&np.isfinite(delta)&np.isfinite(frame.base_spread)&np.isfinite(frame.official_yield)
    return delta,mask
