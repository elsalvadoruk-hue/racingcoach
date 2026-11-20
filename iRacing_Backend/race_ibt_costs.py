"""Private, driver-specific fixed-distance reference passages from iRacing disk telemetry."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from race_corner_cost import CornerCosts, num


def profile_key(identity, driver):
    fields = [driver, identity.get('car_path'), identity.get('track_path'), identity.get('track_config') or '']
    if not all(fields[:3]):
        return None
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def lap_digest(identity):
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]


def extract(path):
    import numpy as np
    from ibt_reader import IBTReader
    names = ('SessionTime SessionNum Lap LapDistPct Speed Brake Throttle FuelLevel CarDistAhead CarDistBehind '
             'PlayerTrackSurface PlayerCarMyIncidentCount TrackWetness TrackTempCrew PlayerTireCompound '
             'OnPitRoad IsOnTrack SessionFlags').split()
    file = IBTReader(path).parse(requested_channels=names)
    d = file.channels
    length = file.metadata.get('track_length_m')
    info = file.session_info.get('DriverInfo') or {}
    own = next((c for c in info.get('Drivers',[]) if c.get('CarIdx')==info.get('DriverCarIdx')),{})
    key = profile_key(file.metadata,own.get('UserID'))
    weekend = file.session_info.get('WeekendInfo') or {}
    session = weekend.get('SubSessionID') or weekend.get('SessionID')
    if not key or not length or any(k not in d for k in names):
        return []
    start = np.flatnonzero(np.r_[True, (np.diff(d['Lap'])!=0)|(np.diff(d['SessionNum'])!=0)])
    result = []
    for first, last in zip(start, [*start[1:],len(d['Lap'])]):
        sl = slice(first,last)
        pct,t,b,gas = [d[k][sl] for k in ('LapDistPct','SessionTime','Brake','Throttle')]
        if len(pct)<40 or np.any(np.diff(pct)<-.001) or np.any(np.diff(t)<=0):
            continue
        if session:
            identity = [int(session),int(d['SessionNum'][first]),int(d['Lap'][first])]
        else:
            # Offline sessions can have zero native IDs. Hash the same points of
            # a full driven lap so copies and overlapping exports count once.
            if pct[0]>.01 or pct[-1]<.99:
                continue
            checkpoints = np.searchsorted(pct,np.arange(.05,.99,.05))
            identity = [key,int(d['Lap'][first]),t[checkpoints].round(3).tolist(),
                        gas[checkpoints].round(3).tolist()]
        lap_key = lap_digest(identity)
        for index in np.flatnonzero((b[1:]>=.08)&(b[:-1]<.08))+1:
            if np.max(b[index:index+max(1,file.tick_rate//2)])<.25:
                continue
            # Identical real-distance gates for both techniques, across recordings.
            anchor = round(float(pct[index])*length/80)*80
            a,z = (anchor-160)/length,(anchor+280)/length
            if a<=pct[0] or z>=pct[-1]:
                continue
            left,right = np.searchsorted(pct,[a,z])
            if not (left<index<right) or right-left<8:
                continue
            idx = slice(first+left,first+right+1)
            if (not np.isfinite(t[left:right+1]).all() or np.max(np.diff(t[left:right+1]))>.2
                    or np.any(d['OnPitRoad'][idx]) or not np.all(d['IsOnTrack'][idx])
                    or np.any(d['SessionFlags'][idx].astype('int64') & 0xBC119)
                    or np.any(d['PlayerTrackSurface'][idx]!=3) or np.ptp(d['PlayerCarMyIncidentCount'][idx])
                    or any(np.any(~np.isfinite(d[k][idx]) | (d[k][idx]<100)) for k in ('CarDistAhead','CarDistBehind'))
                    or np.min(d['Speed'][idx])<8 or np.min(gas[max(index,right-file.tick_rate//2):right+1])<.65
                    or np.any(b[left:index]>.12)):
                continue
            # Stop timing at the start of brake pressure. Including the ramp
            # between 2.5% and 8% would erase genuine lift-and-coast passes.
            unbraked = np.flatnonzero(b[left:index]<=.025)
            if not len(unbraked):
                continue
            brake_start = left+unbraked[-1]+1
            active = np.flatnonzero((gas[left:brake_start]>.05)|(b[left:brake_start]>.025))
            if not len(active) or gas[left+active[-1]]<=.05 or np.max(gas[left:index])<.55:
                continue
            release = left+active[-1]+1
            fuel0,fuel1=np.interp([a,z],pct[left-1:right+1],d['FuelLevel'][first+left-1:first+right+1])
            t0,t1=np.interp([a,z],pct[left-1:right+1],t[left-1:right+1])
            if not 0<fuel0-fuel1<5 or not 1<t1-t0<60:
                continue
            result.append([key,anchor,round(length,1),lap_key,round(float(fuel0),3),int(d['TrackWetness'][idx][0]),
                           int(d['PlayerTireCompound'][idx][0]),round(float(d['TrackTempCrew'][idx][0]),1),
                           round(float(fuel0-fuel1),4),round(float(t1-t0),3),round(float(t[brake_start]-t[release]),3),
                           round(float(d['Speed'][first+left]),2),round(float(d['Speed'][first+right]),2)])
    return result


class IbtCornerCosts:
    def __init__(self, path=None):
        self.groups = defaultdict(lambda: defaultdict(list))
        if path is None:
            return
        try:
            if not path.is_file() or path.stat().st_size>32*1024*1024:
                return
            payload = json.loads(path.read_bytes())
            if payload.get('version') != 2:
                return
            for key,anchor,length,lap,fuel,wet,compound,temp,used,seconds,coast,entry_speed,exit_speed in payload['rows']:
                if not isinstance(key,str) or not isinstance(lap,str) or any(num(v) is None for v in
                        (anchor,length,fuel,wet,compound,temp,used,seconds,coast,entry_speed,exit_speed)) or length<=0:
                    raise ValueError('Invalid passage')
                self.groups[key][anchor/length].append(dict(fuel=fuel,wet=wet,compound=compound,temp=temp,
                    lap=lap,used=used,time=seconds,coast=coast,entry_speed=entry_speed,exit_speed=exit_speed,
                    clear=True,off=False,penalty=False,points=0,braking=True))
        except (ValueError,TypeError,KeyError,OSError,AttributeError):
            self.groups.clear()

    def reference_windows(self, key, zones, length, identity=None):
        """Continue the historical comparison through exactly the same distance gates live."""
        costs = CornerCosts()
        if not length or not zones:
            return costs
        if identity:
            session = identity.get('subsession_id') or identity.get('session_id')
            if str(session).isdigit() and identity.get('session_num') is not None:
                costs.native_lap = lambda lap:lap_digest([int(session),int(identity['session_num']),int(lap)])
        groups=self.groups.get(key,{})
        for z in zones:
            anchor=min(groups,key=lambda a:abs(z['entry']-a),default=None)
            if anchor is None or abs(z['entry']-anchor)*length>65:
                continue
            window = dict(z,start=anchor-160/length, finish=anchor+280/length)
            if not window['start']<z['entry']<z['end']<window['finish']:
                continue
            costs.zones.append(window)
            costs.records[z['key']].extend(groups[anchor])
        return costs


def main():
    import argparse
    from app_settings import data_dir
    parser=argparse.ArgumentParser(description='Build local personal lift-and-coast references')
    parser.add_argument('folder',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    rows,files,errors={},0,0
    for path in sorted(args.folder.rglob('*.ibt')):
        files+=1
        try:
            for row in extract(path):
                rows.setdefault(tuple(row[:4]),row)
        except (OSError,ValueError,KeyError,IndexError):
            errors+=1
    dest=args.output or data_dir()/'race-ibt-costs.json'
    if not files or errors:
        raise SystemExit(f'{files} files, {errors} unreadable')
    dest.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=dest.parent,delete=False) as tmp:
        json.dump(dict(version=2,rows=list(rows.values())),tmp,allow_nan=False,separators=(',',':'))
    Path(tmp.name).replace(dest)
    print(f'{files} IBTs, {len(rows)} independent corner passages')


if __name__=='__main__':
    main()
