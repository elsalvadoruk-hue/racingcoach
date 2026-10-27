"""Seven optional input axes from complete, bounded native input episodes.

No original recording, SDK or store is opened here. Timing uncertainty is two
native channel periods; the interpolated LMU axis never improves that bound.
"""
from __future__ import annotations

from statistics import median
import math

import numpy as np


AXES = {
    'brake_application': dict(short_label='제동 전개', unit='s', names=['점진 제동형','급속 제동형'],
        meaning='한 제동 입력이 그 구간 최대값의 20%에서 80%까지 올라간 시간입니다.',
        example='최대 브레이크가 80%인 입력에서는 16%에서 64%까지의 시간을 읽습니다.',
        interpretation='0.45초 이상 전개와 0.20초 이하 전개를 시간 오차까지 확인합니다. 제동 성과나 코너 위치는 뜻하지 않습니다.',
        calculation='최대 입력 30% 이상인 완전한 제동 구간의 상대 20→80% 전개시간 중앙값. 점진: 시간−오차≥0.45초, 급속: 시간+오차≤0.20초.'),
    'brake_release': dict(short_label='제동 해제', unit='s', names=['점진 해제형','빠른 해제형'],
        meaning='한 제동 입력이 최대값의 80%에서 20%까지 내려간 시간입니다.',
        example='브레이크를 오래 유지했는지가 아니라 페달을 놓는 구간의 길이를 읽습니다.',
        interpretation='0.45초 이상 해제와 0.20초 이하 해제를 구분합니다. 제동·조향 겹침이나 차량 균형과 별개입니다.',
        calculation='완전한 제동 구간의 마지막 80→20% 해제시간 중앙값. 점진: 시간−오차≥0.45초, 빠름: 시간+오차≤0.20초.'),
    'throttle_modulation': dict(short_label='가속 재조절', unit='회/구간', names=['일정 가속 유지형','가속 재조절형'],
        meaning='한 스로틀 입력 중 12%p 이상 줄였다 다시 올린 횟수입니다.',
        example='40→80→55→80%로 입력했다면 한 번의 재조절로 셉니다. 마지막 페달 해제는 세지 않습니다.',
        interpretation='완전한 입력 구간마다 재조절이 없는 경우와 있는 경우를 구분합니다. 재조절 이유나 실제 가속 성과는 추측하지 않습니다.',
        calculation='최대 스로틀 60% 이상인 완전한 구간에서 12%p 하강·회복과 최소 max(0.15초, 원본 3주기) 간격을 확인한 횟수의 중앙값.'),
    'steering_application': dict(short_label='조향 전개', unit='s', names=['점진 조향형','빠른 조향형'],
        meaning='한 방향의 조향 입력이 그 구간 최대 크기의 20%에서 80%까지 커진 시간입니다.',
        example='핸들을 많이 돌렸는지보다 입력이 커지는 속도를 시간으로 읽습니다.',
        interpretation='0.60초 이상 전개와 0.25초 이하 전개를 구분합니다. 게임별 조향각 자체를 비교하거나 코너 위치를 판정하지 않습니다.',
        calculation='최대 조향 0.12rad 이상(iRacing) 또는 잠금범위 4% 이상(LMU)인 완전한 구간의 상대 20→80% 시간 중앙값. 시간 오차를 뺀 0.60초/더한 0.25초 기준.'),
    'steering_corrections': dict(short_label='조향 재조절', unit='회/구간', names=['조향 유지형','조향 재조절형'],
        meaning='한 방향 조향의 주요 전개 뒤 크기를 줄였다 다시 키운 횟수입니다.',
        example='조향을 30→20→30도로 되돌렸다 다시 넣으면 한 번입니다. 마지막 정상적인 풀림은 세지 않습니다.',
        interpretation='작은 떨림과 마지막 조향 해제를 제외합니다. 언더스티어·오버스티어나 실수를 단정하지 않습니다.',
        calculation='최대 조향의 80% 도달 뒤, max(최대값 15%, 0.04rad 또는 LMU 잠금범위 1%) 이상 감소·회복하고 max(0.20초, 원본 3주기) 간격인 재조절 횟수 중앙값.'),
    'coasting': dict(short_label='타력 입력', unit='%', names=['타력 활용형','페달 전환형'],
        meaning='관측한 주행시간 중 브레이크·스로틀을 모두 거의 입력하지 않은 시간의 비율입니다.',
        example='같은 관측시간 100초 중 두 페달 3% 이하가 8초 이어졌다면 8%입니다.',
        interpretation='36km/h를 넘는 구간만 봅니다. 0.40초 이상 두 페달을 비운 전환과 0.15초 이하 전환의 반복을 구분하며 전략이나 효율을 뜻하지 않습니다.',
        calculation='36km/h 초과·트랙 위·피트 밖에서 두 페달 3% 이하가 0.40초 이상 지속된 시간 ÷ 같은 조건의 페달 관측시간. 이름은 완전한 페달 전환의 지속시간과 원본 오차로 구분.'),
    'shift_consistency': dict(short_label='변속 회전수', unit='%', names=['일정 회전수 변속형','변속 회전수 분산형'],
        meaning='같은 기어로 상향 변속할 때 직전 RPM이 얼마나 흩어졌는지 읽습니다.',
        example='2→3단 RPM끼리, 3→4단 RPM끼리 따로 비교합니다. 서로 다른 기어의 RPM을 섞지 않습니다.',
        interpretation='한 랩에서 같은 목표 기어의 변속이 최소 3회 있어야 합니다. MAD 비율과 원본 주기의 RPM 오차 범위를 확인해 2% 이하·5% 이상을 구분합니다. 최적 변속점이나 성능은 판단하지 않습니다.',
        calculation='속도 36km/h 초과·스로틀 60% 이상·브레이크 5% 이하인 안정된 한 단 상향변속 직전 RPM. 같은 목표 기어별 MAD÷중앙값×100을 계산하고 기어별 비율의 중앙값을 표시. RPM 오차는 변속 직전 관측 기울기×원본 2주기로 보수적으로 계산.'),
}


def empty_axis(reason=None):
    return dict(event_count=0,positive_count=0,negative_count=0,value=None,sampling_hz=None,
                timing_uncertainty_s=None,reason_codes=[reason] if reason else [])


def empty_axes(reason=None):
    return {name:empty_axis(reason) for name in AXES}


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def _runs(active):
    indices=np.flatnonzero(active)
    if not len(indices):
        return []
    return zip(indices[np.r_[True,np.diff(indices)!=1]],indices[np.r_[np.diff(indices)!=1,True]])


def _reversals(time, values, amplitude, hold):
    """One decrease followed by a recovery; an unreturned final release is 0."""
    if not len(values):
        return 0
    peak=float(values[0]);trough=peak;peak_at=0;trough_at=0;falling=False;count=0
    for index in range(1,len(values)):
        value=float(values[index])
        if not falling:
            if value>=peak:
                peak=value;peak_at=index
            elif peak-value>=amplitude:
                falling=True;trough=value;trough_at=index
        elif value<trough:
            trough=value;trough_at=index
        elif value-trough>=amplitude:
            if time[index]-time[peak_at]+1e-9>=2*hold and time[index]-time[trough_at]+1e-9>=hold:
                count+=1
                peak=value;peak_at=index;falling=False
            elif value>=peak-amplitude*.1:
                # A fully recovered sub-period blip is discarded. A genuine
                # slower recovery may first cross amplitude before hold; keep
                # following it rather than losing the entire excursion.
                peak=value;peak_at=index;falling=False
    return count


def measure_extended(telemetry,start,end,*,time,eligible,game,rate,sampling):
    channels=telemetry.channels
    count=len(channels['SessionTime'])
    result=empty_axes()
    cache={}

    def array(name):
        if name not in cache:
            try:
                raw=np.asarray(channels[name])
                cache[name]=(raw[start:end+1].astype(float,copy=False) if raw.ndim==1
                    and len(raw)==count and raw.dtype.kind in 'biuf' else None)
            except (KeyError,ValueError,TypeError,OverflowError):
                cache[name]=None
        return cache[name]

    def prepare(name,names):
        axis=result[name]
        values={key:array(key) for key in names}
        if any(value is None for value in values.values()):
            codes={'RPM':'RPM_INPUT_UNAVAILABLE','Gear':'GEAR_INPUT_UNAVAILABLE','Brake':'BRAKE_INPUT_UNAVAILABLE',
                   'Throttle':'THROTTLE_INPUT_UNAVAILABLE','SteeringWheelAngle':'STEERING_INPUT_UNAVAILABLE'}
            axis['reason_codes']=[codes.get(key,'INPUT_CHANNEL_UNAVAILABLE') for key,value in values.items() if value is None]
            return None
        rates=[float(rate)]
        if game=='lmu':
            native=sampling.get('native_hz',{});units=sampling.get('native_units',{})
            for key in ('Speed',*names):
                if key=='Gear':
                    if sampling.get('gear_events_available') is not True or not _finite(sampling.get('gear_event_start_s')) or time[0]<sampling['gear_event_start_s']:
                        axis['reason_codes']=['GEAR_EVENT_EVIDENCE_UNAVAILABLE'];return None
                    continue
                expected=({'km/h','kmh','kph'} if key=='Speed' else {'rpm','rev/min','1/min'} if key=='RPM' else {'%'})
                hz=native.get(key)
                if str(units.get(key,'')).strip().casefold() not in expected:
                    axis['reason_codes']=['INPUT_UNITS_UNCONFIRMED'];return None
                if not _finite(hz) or not 10<=hz<=2000:
                    axis['reason_codes']=['INPUT_SAMPLING_RATE_INSUFFICIENT'];return None
                rates.append(float(hz))
        native_rate=min(rates)
        axis.update(sampling_hz=native_rate,timing_uncertainty_s=round(2./native_rate,6))
        valid=eligible.copy()
        pits=array('OnPitRoad')
        if pits is None:
            axis['reason_codes']=['PIT_STATE_UNAVAILABLE'];return None
        valid &= pits==0
        for key,value in values.items():
            valid &= np.isfinite(value)
            if key in {'Brake','Throttle'}:valid &= (value>=0)&(value<=1)
            if key=='Gear':valid &= (value>=1)&(value<=10)&(value==np.floor(value))
            if key=='RPM':valid &= (value>=500)&(value<=30000)
            if key=='SteeringWheelAngle':valid &= np.abs(value)<=30
        if not np.all(valid|~eligible):axis['reason_codes'].append('INVALID_OR_EXCLUDED_INPUT_SAMPLES')
        return values,valid,native_rate

    def complete(values,valid,threshold,peak_min):
        for left,right in _runs(valid&(values>threshold)):
            if left==0 or right==len(time)-1 or not valid[left-1] or not valid[right+1]:continue
            if not .15<=time[right+1]-time[left]<=30:continue
            peak=float(np.max(values[left:right+1]))
            if peak>=peak_min:yield int(left),int(right),peak

    def summarize(axis,values,positive,negative):
        axis.update(event_count=len(values),positive_count=positive,negative_count=negative,
                    value=round(float(median(values)),6) if values else None)
        if not values:axis['reason_codes'].append('NO_COMPLETE_INPUT_EPISODES')

    for name,release in (('brake_application',False),('brake_release',True)):
        prepared=prepare(name,('Brake',))
        if not prepared:continue
        values,valid,native_rate=prepared;brake=values['Brake'];axis=result[name];durations=[];pro=con=0
        for left,right,peak in complete(brake,valid,.05,.3):
            segment=brake[left-1:right+2]
            if not release:
                high=left-1+int(np.flatnonzero(segment>=.8*peak)[0])
                below=np.flatnonzero(brake[left-1:high+1]<=.2*peak)
                if not len(below):continue
                low=left-1+int(below[-1])
            else:
                high=left-1+int(np.flatnonzero(segment>=.8*peak)[-1])
                below=np.flatnonzero(brake[high:right+2]<=.2*peak)
                if not len(below):continue
                low=high+int(below[0])
            duration=float(abs(time[high]-time[low]))
            if not 0<duration<=8:continue
            durations.append(duration);error=2./native_rate
            pro+=int(duration-error+1e-9>=.45);con+=int(duration+error<=.20+1e-9)
        summarize(axis,durations,pro,con)

    prepared=prepare('throttle_modulation',('Throttle','Brake'))
    if prepared:
        values,valid,native_rate=prepared;throttle=values['Throttle'];valid &= values['Brake']<=.05
        counts=[]
        for left,right,peak in complete(throttle,valid,.1,.6):
            counts.append(_reversals(time[left:right+1],throttle[left:right+1],.12,max(.15,3./native_rate)))
        summarize(result['throttle_modulation'],counts,sum(value==0 for value in counts),sum(value>=1 for value in counts))

    for name in ('steering_application','steering_corrections'):
        prepared=prepare(name,('SteeringWheelAngle',))
        if not prepared:continue
        values,valid,native_rate=prepared;signed=values['SteeringWheelAngle'];axis=result[name]
        threshold=.025;minimum=.12;noise=.04
        if game=='lmu':
            half=sampling.get('steering_half_range_rad')
            if not _finite(half) or half<=0:axis['reason_codes'].append('STEERING_SCALE_UNAVAILABLE');continue
            signed=signed/half;valid &= np.abs(signed)<=1.05;threshold=.01;minimum=.04;noise=.01
        measurements=[];pro=con=0
        turns=[]
        for direction in (-1.,1.):
            steer=np.maximum(signed*direction,0.)
            turns.extend((left,right,peak,steer) for left,right,peak in complete(steer,valid,threshold,minimum))
        for left,right,peak,steer in turns:
            high=left-1+int(np.flatnonzero(steer[left-1:right+2]>=.8*peak)[0])
            if name=='steering_application':
                below=np.flatnonzero(steer[left-1:high+1]<=.2*peak)
                if not len(below):continue
                low=left-1+int(below[-1]);duration=float(time[high]-time[low])
                if not 0<duration<=8:continue
                measurements.append(duration);error=2./native_rate
                pro+=int(duration-error+1e-9>=.60);con+=int(duration+error<=.25+1e-9)
            else:
                reductions=_reversals(time[high:right+1],steer[high:right+1],max(noise,.15*peak),max(.20,3./native_rate))
                measurements.append(reductions);pro+=int(reductions==0);con+=int(reductions>=1)
        summarize(axis,measurements,pro,con)

    prepared=prepare('coasting',('Throttle','Brake'))
    if prepared:
        values,valid,native_rate=prepared;valid &= array('Speed')>10.
        coast=valid&(values['Throttle']<=.03)&(values['Brake']<=.03)
        seconds=0.;events=pro=con=0;error=2./native_rate
        for left,right in _runs(coast):
            duration=float(time[right]-time[left])
            if duration+1e-9>=.4:seconds+=duration
            if left==0 or right==len(time)-1 or not valid[left-1] or not valid[right+1]:continue
            if duration<max(1./native_rate,.02) or duration>30:continue
            events+=1;pro+=int(duration-error+1e-9>=.4);con+=int(duration+error<=.15+1e-9)
        observed=float(np.sum(np.diff(time)[valid[:-1]&valid[1:]]))
        # A continuous observed window supports a measured zero/ratio even
        # without a complete handoff. It contributes no directional vote.
        if observed and not events:events=1
        axis=result['coasting'];axis.update(event_count=events,positive_count=pro,negative_count=con,
            value=round(seconds/observed*100.,6) if events and observed else None,
            coasting_time_s=round(seconds,6),observed_time_s=round(observed,6))
        if not events or not observed:axis['reason_codes'].append('NO_COMPLETE_PEDAL_TRANSITIONS')

    prepared=prepare('shift_consistency',('Gear','RPM','Throttle','Brake'))
    if prepared:
        values,valid,native_rate=prepared;gear=values['Gear'];rpm=values['RPM'];groups={}
        valid &= (array('Speed')>10.)&(values['Throttle']>=.6)&(values['Brake']<=.05)
        hold=max(.15,3./native_rate)
        for index in np.flatnonzero(np.diff(gear)==1)+1:
            left=int(np.searchsorted(time,time[index]-hold));right=int(np.searchsorted(time,time[index]+hold,side='right'))
            if not 0<left<index<right<len(time) or not np.all(valid[left:right]):continue
            if not np.all(gear[left:index]==gear[index-1]) or not np.all(gear[index:right]==gear[index]):continue
            slopes=np.abs(np.diff(rpm[left:index])/np.diff(time[left:index]))
            uncertainty=float(np.max(slopes))*2./native_rate if len(slopes) else 0.
            groups.setdefault(int(gear[index]),[]).append((float(rpm[index-1]),uncertainty))
        summaries=[]
        for gear_number,samples in sorted(groups.items()):
            if len(samples)<3:continue
            rpms=[row[0] for row in samples]
            center=median(rpms);spread=median(abs(value-center) for value in rpms)/center*100.
            uncertainty=max(row[1] for row in samples)/center*100.
            summaries.append(dict(target_gear=gear_number,count=len(rpms),median_rpm=round(center,6),
                                  mad_pct=round(spread,6),rpm_uncertainty_pct=round(uncertainty,6)))
        axis=result['shift_consistency'];axis['gear_groups']=summaries
        axis.update(event_count=sum(row['count'] for row in summaries),
            positive_count=sum(row['count'] for row in summaries if row['mad_pct']+row['rpm_uncertainty_pct']<=2.),
            negative_count=sum(row['count'] for row in summaries if row['mad_pct']-row['rpm_uncertainty_pct']>=5.),
            value=round(median(row['mad_pct'] for row in summaries),6) if summaries else None)
        if not summaries:axis['reason_codes'].append('INSUFFICIENT_SAME_GEAR_SHIFTS')
    return result


def validate_extended(name,axis,fail):
    if not isinstance(axis,dict):fail()
    checked={}
    for key in ('event_count','positive_count','negative_count'):
        value=axis.get(key)
        if type(value) is not int or not 0<=value<=1000000:fail()
        checked[key]=value
    if checked['positive_count']+checked['negative_count']>checked['event_count']:fail()
    for key,maximum in (('value',100. if AXES[name]['unit']=='%' else 8. if AXES[name]['unit']=='s' else 1000.),
                        ('sampling_hz',2000.),('timing_uncertainty_s',1.)):
        value=axis.get(key)
        if value is not None and (not _finite(value) or not 0<=value<=maximum):fail()
        checked[key]=value
    if checked['event_count'] and (checked['value'] is None or not checked['sampling_hz'] or checked['timing_uncertainty_s'] is None):fail()
    if checked['event_count'] and (checked['sampling_hz']<10 or abs(checked['timing_uncertainty_s']-2./checked['sampling_hz'])>.00001):fail()
    if checked['event_count'] and AXES[name]['unit']=='s' and checked['value']<=0:fail()
    if not checked['event_count'] and checked['value'] is not None:fail()
    reasons=axis.get('reason_codes',[])
    import re
    if not isinstance(reasons,list) or len(reasons)>40 or any(not isinstance(reason,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,79}',reason) for reason in reasons):fail()
    checked['reason_codes']=sorted(set(reasons))
    if name=='coasting' and checked['event_count'] and 'observed_time_s' not in axis:fail()
    if name=='coasting' and 'observed_time_s' in axis:
        coast,seconds=axis.get('coasting_time_s'),axis.get('observed_time_s')
        if not _finite(coast) or not _finite(seconds) or not 0<=coast<=seconds<=86400:fail()
        if checked['event_count'] and (seconds<=0 or abs(checked['value']-coast/seconds*100.)>.001):fail()
        checked.update(coasting_time_s=coast,observed_time_s=seconds)
    if name=='shift_consistency' and checked['event_count'] and 'gear_groups' not in axis:fail()
    if name=='shift_consistency' and 'gear_groups' in axis:
        groups=axis['gear_groups'];seen=set();valid=[]
        if not isinstance(groups,list) or len(groups)>9:fail()
        for row in groups:
            if not isinstance(row,dict):fail()
            gear,count,rpm,spread=(row.get(key) for key in ('target_gear','count','median_rpm','mad_pct'))
            uncertainty=row.get('rpm_uncertainty_pct',0.)
            if type(gear) is not int or not 2<=gear<=10 or gear in seen or type(count) is not int or not 3<=count<=1000000:fail()
            if not _finite(rpm) or not 500<=rpm<=30000 or not _finite(spread) or not 0<=spread<=100:fail()
            if not _finite(uncertainty) or not 0<=uncertainty<=1000000000:fail()
            seen.add(gear);item=dict(target_gear=gear,count=count,median_rpm=rpm,mad_pct=spread)
            if 'rpm_uncertainty_pct' in row:item['rpm_uncertainty_pct']=uncertainty
            valid.append(item)
        if sum(row['count'] for row in valid)!=checked['event_count']:fail()
        if valid and (abs(median(row['mad_pct'] for row in valid)-checked['value'])>.001
            or sum(row['count'] for row in valid if row['mad_pct']+row.get('rpm_uncertainty_pct',0.)<=2.)!=checked['positive_count']
            or sum(row['count'] for row in valid if row['mad_pct']-row.get('rpm_uncertainty_pct',0.)>=5.)!=checked['negative_count']):fail()
        checked['gear_groups']=valid
    return checked
