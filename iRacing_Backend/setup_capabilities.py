"""Documented wheel/keyboard adjustments. Unknown cars/options stay unknown.

F7/pit permission is not inferred from a garage setup, nor does a dp* index
represent a physical angle. Session strings are reduced to bounded numbers.
"""
import re
from live_controls import finite

MANUALS='https://www.iracing.com/resources/user-manuals/'
S24='https://support.iracing.com/support/solutions/articles/31000173378-2024-season-3-release-notes-2024-06-03-01-'
S26='https://support.iracing.com/support/solutions/articles/31000179016-2026-season-3-initial-release-notes-2026-06-09-01-'
S4='https://support.iracing.com/support/solutions/articles/31000179517-2026-season-4-initial-release-notes-2026-09-09-01-'
GT3=('acuransxevo22gt3','amvantageevogt3','audir8lmsevo2gt3','bmwm4gt3','chevyvettez06rgt3',
     'ferrari296gt3','fordmustanggt3','lamborghinievogt3','mclaren720sgt3','mercedesamgevogt3','porsche992rgt3')
GTE=('bmwm8gte','c8rvettegte','ferrari488gte','fordgt2017','porsche991rsr')
GTP=('acuraarx06gtp','bmwlmdh','cadillacvseriesgtp','porsche963gtp')
WHEELS=('lf','rf','lr','rr')
LABELS={'front_arb':'앞 ARB','rear_arb':'뒤 ARB','rear_wing':'리어 윙',
        **dict(zip(('pressure_'+w for w in WHEELS),('좌전 공기압','우전 공기압','좌후 공기압','우후 공기압')))}


def profile_for(identity):
    if identity.get('game')!='iracing':return {}
    car=str(identity.get('car_path') or '').strip('/\\').lower().replace('\\','/')
    result={}
    def arb(timing,source,**extra):
        return {key:dict(timing=timing,source=source,**extra) for key in ('front_arb','rear_arb')}
    if car in GT3:result={**arb('pit',S24),'rear_wing':dict(timing='pit',source=S4,unit='°')}
    elif car in GTE:result=arb('pit',S26)
    elif car in GTP:result=arb('live',S24,straight_only=True)
    elif car=='dallarair18':result=arb('live',MANUALS,soft_to_stiff=list(range(1,7)))
    elif car=='superformulalights324':result={'front_arb':dict(timing='live',source=MANUALS,soft_to_stiff=list(range(1,6)))}
    elif car=='porschemissionr':
        # Manual p12 contradicts itself for the intermediate rear positions.
        result={'rear_arb':dict(timing='live',source=MANUALS)}
    elif car in ('porsche992cup','porsche9922cup'):
        result={**arb('pit',S24 if car=='porsche992cup' else S4),
                'rear_wing':dict(timing='pit',source=S4 if car=='porsche9922cup' else S24,unit='°')}
        if car=='porsche9922cup':
            result['front_arb']['soft_to_stiff']=[1,2,3,4,7,5,6]
            result['rear_arb']['soft_to_stiff']=[1,2,3,4,5,7,6]
    elif car=='ligierjsp320':result={'rear_wing':dict(timing='pit',source=S4,unit='°')}
    if result:
        result.update({'pressure_'+w:dict(timing='pit',source=MANUALS,unit='kPa') for w in WHEELS})
    return result


def session_setup_values(setup):
    """Only actual garage values; no usernames, arbitrary metadata or raw text."""
    result={};budget=[256]
    def visit(node,parents=(),depth=0):
        if not isinstance(node,dict) or depth>6:return
        for key,value in node.items():
            budget[0]-=1
            if budget[0]<0:return
            name=re.sub('[^a-z]','',str(key).lower())
            path=parents+(name,)
            if isinstance(value,dict):visit(value,path,depth+1);continue
            text=str(value) if type(value) in (str,int,float) else ''
            match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(kPa|psi|deg|°)?\s*',text,re.I)
            if not match:continue
            number=finite(float(match[1]));unit=(match[2] or '').lower()
            if number is None:continue
            front={'front','frontarb','frontbrakes','frontbrakeslights'}
            rear={'rear','reararb','rearwing'}
            axle=next(('front' if p in front else 'rear' for p in reversed(parents) if p in front|rear),None)
            if name in ('arbsetting','arbblades','arbbladeposition','arb') and axle and not unit:
                result[axle+'_arb']=number
            elif name in ('wingsetting','wingangle','rearwingangle') and axle=='rear' and unit in ('deg','°'):
                result['rear_wing']=number
            elif name in ('coldpressure','coldpress','tirecoldpress','tyrecoldpress','startingpressure') and unit in ('kpa','psi'):
                wheel=next(({'leftfront':'lf','rightfront':'rf','leftrear':'lr','rightrear':'rr'}.get(p)
                            for p in reversed(parents) if p in ('leftfront','rightfront','leftrear','rightrear')),None)
                if wheel and number>0:result['pressure_'+wheel]=round(number*(6.894757293 if unit=='psi' else 1),5)
    visit(setup)
    return result


def read_setup_channels(sdk,names):
    result={}
    for key,name in (('front_arb_raw','dcAntiRollFront'),('rear_arb_raw','dcAntiRollRear')):
        if name in names:result[key]=finite(sdk[name])
    for w in WHEELS:
        name='PitSv'+w.upper()+'P'
        if name in names:result['pressure_'+w+'_pending']=finite(sdk[name])
    return result


def fixed_setup(weekend):
    options=weekend.get('WeekendOptions') or {}
    value=options.get('IsFixedSetup',options.get('FixedSetup'))
    if value in (0,'0',False):return False
    if value in (1,'1',True):return True
    return None
