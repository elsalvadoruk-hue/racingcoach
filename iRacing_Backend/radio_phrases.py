"""Engineer radio wording: several ways to say one thing, with measured numbers.

Every call is one whole sentence group synthesized as a single clip. Joining
separately synthesized fragments sounded broken, and a number followed by a
comma ran into the next word, so a number always ends its sentence: a full
stop follows it (or a short approved ending such as "입니다."). Commas are not
used in spoken text. Only the program's measured values fill the placeholders.
"""
import itertools
import re
import radio_variations


def lap_time(seconds):
    """102.34 -> '1분 42초 3' (tenths), the form the driver chose for lap times."""
    tenths_total = round(seconds * 10)
    minutes, rest = divmod(tenths_total, 600)
    return (f'{minutes}분 ' if minutes else '') + f'{rest // 10}초 {rest % 10}'


FORMATS = {
    'gap': lambda v: f'{v:.1f}초', 'pos': lambda v: f'{int(v)}위', 'points': lambda v: f'{int(v)}점',
    'left': lambda v: f'{int(v)}점', 'liters': lambda v: f'{round(v)}리터', 'need': lambda v: f'{round(v)}리터',
    'deg': lambda v: f'{round(v)}도', 'lap': lap_time,
    'loss': lambda v: f'{v:.1f}초', 'min': lambda v: f'{int(v)}분', 'laps': lambda v: f'{int(v)}랩', 'sec': lambda v: f'{round(v)}초',
    'short': lambda v: f'{round(v)}리터', 'fuel': lambda v: f'{v:.1f}리터', 'target': lambda v: f'{v:.1f}리터',
    'turn': str, 'place': str,
    'kph': lambda v: f'{round(v)}킬로미터', 'steer': lambda v: f'{v:.1f}도',
}


def tenths(start, stop):
    return [value / 10 for value in range(round(start * 10), round(stop * 10) + 1)]


# Values prepared ahead of a drive. Anything outside is synthesized once, on first use.
RANGES = {
    'gap': tenths(.1, 9.9), 'pos': range(1, 61), 'points': range(1, 41), 'left': range(1, 9), 'deg': range(4, 21),
    # Lap times around GT3 pace: ordinary circuits, Le Mans, and the Nordschleife layouts.
    'lap': tenths(40, 210) + tenths(230, 270) + tenths(390, 570),
    'loss': tenths(.1, 3.0), 'min': (1, 2, 5, 10), 'laps': (1, 2, 5, 10), 'sec': range(5, 181), 'short': range(1, 61),
}
# Calls with two numbers are prepared over a narrower grid; None means first use only.
PLACES = [f'{n}번 코너 부근' for n in range(1, 31)] + [f'{n}번 구간' for n in range(1, 9)]

PREPARED = {
    'quali_result': {'pos': range(1, 41), 'gap': tenths(.1, 3.0)},
    'quali_p1': {'gap': tenths(.1, 3.0)},
    'consistent': {'gap': tenths(.1, .3)},
    'pit_fuel_low': None,
    'pit_fuel_add': {'liters': range(1, 121)}, 'laps_to_go': {'laps': range(1, 61)},
    # After a contact: what changed between the laps before and after it (live_damage).
    'repair_pace_loss': {'loss': tenths(.7, 5.0)},
    'damage_slower': {'loss': tenths(.7, 5.0)}, 'damage_vmax': {'kph': range(2, 31)}, 'damage_steer': {'steer': tenths(.4, 3.0)},
    # Stretches of the lap: a confirmed corner or one of the eight equal sectors. Named corners are synthesized on first use.
    **{key: {'place': PLACES} for key in ('rival_ahead_faster_at', 'rival_ahead_slower_at', 'rival_behind_faster_at', 'rival_behind_slower_at')},
    'brief_best_corner': {'turn': [f'{n}번 코너' for n in range(1, 31)]},
    'catch_in': {'laps': range(1, 31)}, 'caught_in': {'laps': range(1, 31)},
    # One line on a long straight: the worst corner of the last lap, by its confirmed number.
    'brief_corner_loss': {'turn': [f'{n}번 코너' for n in range(1, 31)], 'loss': tenths(.1, 1.0)},
    # The second sentence of the same line: said right after 'brief_corner_loss' as one call.
    'brief_corner_also': {'turn': [f'{n}번 코너' for n in range(1, 31)], 'loss': tenths(.1, 1.0)},
    'pit_fuel_full': None,
    # Per-lap fuel around GT3 use; other cars are synthesized on first use.
    'fuel_lap': {'fuel': tenths(1.5, 4.5), 'target': tenths(1.5, 4.5)},
    # Confirmed turn numbers; named corners (Nordschleife) are synthesized on first use.
    **{f'corner_call_{cause}': {'turn': [f'{n}번 코너' for n in range(1, 31)], 'loss': tenths(.1, 1.0)}
       for cause in ('brake_early', 'min_speed', 'throttle_late', 'time')},
}

PHRASES = {
    # Race picture: only gaps that are shrinking are worth the driver's attention.
    'gap_ahead_closing': ('race', [
        '앞차 {gap}. 줄고 있습니다.',
        '앞차 간격 {gap}. 따라붙고 있습니다.',
        '앞차까지 {gap}. 랩마다 줄이고 있습니다.',
        '좋습니다. 앞차 {gap}. 계속 좁히세요.',
    ]),
    'gap_behind_closing': ('race', [
        '뒤차 {gap}. 가까워지고 있습니다.',
        '뒤차 간격 {gap}. 줄고 있습니다.',
        '뒤에서 옵니다. 간격 {gap}.',
        '뒤차 접근 중. 간격 {gap}. 라인 지키세요.',
    ]),
    'halfway': ('race', ['절반 지났습니다. 클래스 {pos}.', '경기 절반입니다. 현재 클래스 {pos}.', '반환점 통과. 현재 클래스 {pos}.']),
    'last_lap': ('race', ['마지막 랩입니다. 끝까지 깔끔하게.', '라스트 랩. 실수 없이 가져옵시다.', '마지막 랩. 그대로 마무리하세요.']),
    'last_lap_defend': ('race', ['마지막 랩. 뒤차가 바로 뒤에 있습니다.', '라스트 랩. 뒤에 붙어 있습니다. 라인 지키세요.']),
    'last_lap_attack': ('race', ['마지막 랩. 앞차가 바로 앞입니다.', '라스트 랩. 앞차 사정권입니다.']),
    'race_result': ('race', [
        '체커 받았습니다. 클래스 {pos}. 수고하셨습니다.',
        '경기 종료. 클래스 {pos}. 잘 달렸습니다.',
        '끝났습니다. 클래스 {pos}. 고생하셨습니다.',
    ]),
    'consistent': ('race', [
        '페이스 일정합니다. 최근 세 랩 편차 {gap}.',
        '좋은 리듬입니다. 세 랩 편차 {gap}.',
        '최근 세 랩이 일정합니다. 편차 {gap}. 좋습니다.',
    ]),
    'no_repair': ('race', ['수리 필요 신호는 없습니다. 계속 가세요.', '수리 신호 없습니다. 페이스 되찾으세요.', '차량 수리 표시는 없습니다. 계속 갑니다.']),
    'incident_limit': ('race', ['인시던트 {points}. 한도까지 {left}.', '인시던트 누적 {points}. 남은 여유 {left}.']),
    # Pit sequence, counted down from measured fuel.
    'pit_soon': ('strategy', ['피트까지 약 다섯 랩입니다. 피트 입구를 봐 두세요.', '다섯 랩쯤 뒤에 피트합니다.', '피트 준비. 약 다섯 랩 남았습니다.']),
    'pit_two': ('strategy', ['두 랩 안에 피트합니다.', '피트까지 두 랩입니다. 준비하세요.', '곧 피트입니다. 두 랩 남았습니다.']),
    'pit_fuel_low': ('strategy', ['급유 설정은 {liters}입니다. {need} 넣어야 합니다.', '급유량을 확인하세요. 설정은 {liters}입니다. 필요한 양은 {need}입니다.']),
    'pit_fuel_full': ('strategy', ['급유 설정은 {liters}입니다. 가득 채워야 합니다.', '급유량을 확인하세요. 설정은 {liters}입니다. 가득 채워야 합니다.']),
    # Said on a long straight when nothing else is waiting (LiveTelemetry._brief_straight).
    'brief_gap_ahead': ('race', ['앞차와 {gap} 차.', '앞차까지 {gap}입니다.']),
    'brief_gap_behind': ('race', ['뒤차와 {gap} 차.', '뒤차까지 {gap}입니다.']),
    'brief_corner_loss': ('coach', ['지난 랩은 {turn}에서 {loss} 잃었습니다.', '지난 랩 가장 큰 손실은 {turn}입니다. {loss} 잃었습니다.']),
    'brief_corner_also': ('coach', ['{turn}에서도 {loss} 잃었습니다.', '그리고 {turn}에서 {loss} 잃었습니다.']),
    # Labels: a short sentence that says what the next, already prepared number sentence is.
    # They use the spotter's voice because the lap-time sentences ('lap_time_read') exist in that voice.
    'label_leader_lap': ('spotter', ['클래스 선두 지난 랩입니다.', '클래스 선두의 최근 랩입니다.']),
    'label_ahead_lap': ('spotter', ['앞차 지난 랩입니다.', '앞차의 최근 랩입니다.']),
    'label_stint_avg': ('spotter', ['지난 스틴트 평균입니다.', '지난 스틴트 평균 랩타임입니다.']),
    'label_theoretical': ('spotter', ['이론상 베스트입니다.', '구간 최고를 모두 합친 기록입니다.']),
    'theoretical_gain': ('spotter', ['지금 베스트보다 {loss} 빠릅니다.', '베스트에서 {loss} 더 줄일 수 있습니다.']),
    # The lap in progress against the best lap, at the last sector line.
    'delta_ahead': ('coach', ['베스트보다 {loss} 앞서 있습니다.', '지금 랩은 베스트보다 {loss} 빠릅니다.']),
    'delta_behind': ('coach', ['베스트보다 {loss} 뒤져 있습니다.', '지금 랩은 베스트보다 {loss} 느립니다.']),
    'brief_best_corner': ('coach', ['가장 좋았던 곳은 {turn}입니다.', '지난 랩은 {turn}이 가장 좋았습니다.']),
    'brief_gain': ('coach', ['{loss} 줄였습니다.', '구간 최고를 {loss} 줄였습니다.']),
    # Where the driver gains and loses against the car ahead or behind, from sector times.
    'rival_ahead_faster_at': ('race', ['앞차보다 빠른 곳은 {place}입니다.', '{place}에서 앞차보다 빠릅니다.']),
    'rival_ahead_slower_at': ('race', ['앞차보다 느린 곳은 {place}입니다.', '{place}에서는 앞차가 빠릅니다.']),
    'rival_behind_faster_at': ('race', ['뒤차보다 빠른 곳은 {place}입니다.', '{place}에서 뒤차보다 빠릅니다.']),
    'rival_behind_slower_at': ('race', ['뒤차가 빠른 곳은 {place}입니다.', '{place}에서는 뒤차가 빠릅니다.']),
    'by_faster': ('race', ['{loss} 빠릅니다.', '차이는 {loss}입니다.']),
    'by_slower': ('race', ['{loss} 느립니다.', '차이는 {loss}입니다.']),
    # What it takes to catch the car ahead, and how soon the car behind arrives.
    'ahead_far': ('race', ['앞차까지 {sec}입니다.', '앞차와 {sec} 차.']),
    'catch_need': ('race', ['끝까지 닿으려면 랩당 {loss} 빨라야 합니다.', '남은 거리에 따라잡으려면 랩당 {loss} 필요합니다.']),
    'catch_rate': ('race', ['지금은 랩당 {loss}씩 줄이고 있습니다.', '랩마다 {loss}씩 줄이고 있습니다.']),
    'catch_in': ('race', ['{laps} 뒤에 닿습니다.', '이대로면 {laps} 뒤에 따라잡습니다.']),
    'lose_rate': ('race', ['뒤차가 랩당 {loss}씩 좁히고 있습니다.', '뒤차와 랩마다 {loss}씩 줄고 있습니다.']),
    'caught_in': ('race', ['{laps} 뒤에 닿습니다.', '이대로면 {laps} 뒤에 따라잡힙니다.']),
    'pit_rejoin': ('strategy', ['피트하면 클래스 {pos}로 나옵니다.', '지금 피트하면 클래스 {pos}로 복귀합니다.']),
    'damage_same': ('race', ['충돌 뒤 랩타임과 최고속은 그대로입니다.', '충돌 전과 랩타임 차이는 없습니다. 최고속도 그대로입니다.']),
    'damage_slower': ('race', ['충돌 뒤 랩타임이 {loss} 느려졌습니다.', '충돌 전보다 랩당 {loss} 느립니다.']),
    'damage_vmax': ('race', ['직선 최고속이 {kph} 떨어졌습니다.', '최고속이 충돌 전보다 {kph} 낮습니다.']),
    'damage_steer': ('race', ['직선에서 핸들이 {steer} 틀어졌습니다.', '핸들 중심이 {steer} 틀어져 있습니다.']),
    'damage_steer_same': ('race', ['핸들 중심도 그대로입니다.', '직선에서 핸들도 똑바릅니다.']),
    # Stopped in the stall with a repair on offer: its time, the fuel time beside it, and the cost on track so far.
    'repair_optional': ('race', ['선택 수리는 {sec}입니다.', '선택 수리에 {sec} 걸립니다.']),
    'repair_mandatory': ('race', ['필수 수리는 {sec}입니다.', '필수 수리에 {sec} 걸립니다.']),
    'repair_fuel_time': ('race', ['급유는 약 {sec} 걸립니다.', '급유에 약 {sec} 듭니다.']),
    'repair_gain': ('race', ['수리하는 쪽이 약 {sec} 빠릅니다.', '수리하면 완주까지 약 {sec} 이득입니다.']),
    'repair_cost': ('race', ['수리하지 않는 쪽이 약 {sec} 빠릅니다.', '수리하면 완주까지 약 {sec} 손해입니다.']),
    'repair_even': ('race', ['수리해도 안 해도 비슷합니다.', '수리 여부로 시간 차이는 거의 없습니다.']),
    'pit_run_dry': ('strategy', ['연료를 끝까지 쓰고 들어와야 합니다.', '일찍 들어오면 끝까지 못 갑니다.']),
    'box_end_of_lap': ('strategy', ['이번 랩 끝나고 피트합니다.', '이번 랩까지 돌고 피트합니다.']),
    'box_now': ('strategy', ['이번 랩에 피트 들어오세요.', '이번 랩 끝에 박스입니다.']),
    'repair_pace_same': ('race', ['충돌 뒤 랩타임 손실은 없었습니다.', '충돌 뒤에도 랩타임은 그대로였습니다.']),
    'repair_pace_loss': ('race', ['충돌 뒤 랩당 {loss} 느려져 있습니다.', '충돌 뒤 랩타임이 {loss} 느려진 상태입니다.']),
    'tyre_keep': ('strategy', ['타이어는 바꾸지 않습니다.', '타이어는 그대로 갑니다.']),
    'tyre_change': ('strategy', ['타이어도 바꿉니다.', '타이어 교체합니다.']),
    'tyre_setting_on': ('strategy', ['지금 설정은 타이어 교체입니다.', '타이어 교체가 체크되어 있습니다.']),
    'tyre_setting_off': ('strategy', ['지금 설정은 타이어 교체 없음입니다.', '타이어 교체가 빠져 있습니다.']),
    # The fuel for the stop, to the litre, from the counted race distance (RaceEngineer.view).
    'pit_fuel_add': ('strategy', ['급유는 {liters}입니다.', '이번 피트 급유량은 {liters}입니다.']),
    'pit_window_open': ('strategy', ['지금부터는 한 번 급유로 끝까지 갑니다.', '이제 한 번만 서면 끝까지 갑니다.']),
    'finish_laps_more': ('strategy', ['한 랩 늘었습니다. 연료가 모자랍니다.', '경기가 한 랩 길어졌습니다. 급유가 필요합니다.']),
    'laps_to_go': ('strategy', ['{laps} 남았습니다.', '남은 거리는 {laps}입니다.']),
    'fuel_short_by': ('strategy', ['완주까지 {short} 부족합니다.', '이대로는 {short} 모자랍니다.', '연료가 {short} 부족합니다. 급유가 필요합니다.']),
    'fuel_lap': ('strategy', ['이번 랩은 {fuel} 썼습니다. 목표는 {target}입니다.', '이번 랩 연료는 {fuel}입니다. 목표는 {target}입니다.']),
    # Track and weather.
    'track_wet_start': ('race', ['노면이 젖기 시작했습니다.', '비가 노면에 닿고 있습니다. 젖기 시작합니다.', '노면 젖음 감지. 그립 주의하세요.']),
    'track_drying': ('race', ['노면이 마르고 있습니다.', '노면 상태 회복 중입니다.']),
    'track_temp_down': ('race', ['노면 온도가 내려갔습니다. 하락 폭 {deg}.', '노면이 식었습니다. 온도 차 {deg}.']),
    'track_temp_up': ('race', ['노면 온도가 올랐습니다. 상승 폭 {deg}.', '노면이 뜨거워졌습니다. 온도 차 {deg}.']),
    'rain_prepare': ('strategy', [
        '노면이 계속 젖고 있습니다. 다음 피트에서 레인 타이어를 준비하세요.',
        '아직은 유지합니다. 더 젖으면 레인 타이어로 갑니다.',
        '레인 타이어 전환을 준비합니다. 지금은 스테이 아웃.',
    ]),
    'rain_box': ('strategy', [
        '레인 타이어로 바꿀 시점입니다. 이번 랩 피트를 검토하세요.',
        '노면이 많이 젖었습니다. 레인 타이어로 갑니다. 이번 랩 박스를 검토하세요.',
        '레인 타이어 권장합니다. 가능한 빨리 피트하세요.',
    ]),
    'track_very_wet': ('race', ['노면이 많이 젖었습니다. 그립 주의하세요.', '노면에 물이 많습니다. 조심해서 가세요.']),
    'slick_box': ('strategy', ['노면이 말랐습니다. 드라이 타이어 복귀를 검토하세요.', '드라이 타이어 조건입니다. 다음 피트에서 바꿉시다.']),
    # Qualifying: the result of the lap just finished, nothing else.
    'quali_result': ('race', ['클래스 {pos}. 선두와 {gap} 차.', '현재 클래스 {pos}. 폴까지 {gap}.', '현재 {pos}. 선두와 차이 {gap}.']),
    'quali_p1': ('race', ['클래스 1위입니다. 2위와 {gap} 차.', '현재 폴입니다. 뒤와 간격 {gap}.', '가장 빠릅니다. 2위와 차이 {gap}.']),
    'quali_alone': ('race', ['기록 확인했습니다. 현재 클래스 1위입니다.', '랩 기록 들어왔습니다. 클래스 1위.']),
    # The lap time itself, read by the spotter after every completed lap.
    'lap_time_read': ('spotter', ['{lap}입니다.', '랩타임. {lap}.', '이번 랩 {lap}입니다.']),
    'personal_best_read': ('spotter', ['세션 베스트입니다. {lap}입니다.', '베스트 랩 나왔습니다. {lap}입니다.']),
    # Numbers that used to be left on the screen. After listening, the natural
    # sentence ("5분 남았습니다") was preferred to a clipped one ("남은 시간 5분.").
    'time_left': ('spotter', ['{min} 남았습니다.', '경기 종료까지 {min} 남았습니다.', '남은 시간은 {min}입니다.']),
    'laps_left': ('spotter', ['{laps} 남았습니다.', '이제 {laps} 남았습니다.', '경기 종료까지 {laps}입니다.']),
    'position_up': ('spotter', ['클래스 {pos}로 올라섰습니다.', '순위 올랐습니다. 클래스 {pos}입니다.']),
    'position_down': ('spotter', ['클래스 {pos}로 내려갔습니다.', '순위 내려갔습니다. 클래스 {pos}입니다.']),
    'position_now': ('spotter', ['현재 클래스 {pos}입니다.', '클래스 순위는 {pos}입니다.']),
    'tow_time': ('spotter', ['견인 중입니다. {sec} 남았습니다.', '견인 중입니다. 약 {sec} 걸립니다.']),
    'pace_slower_by': ('race', ['최근 페이스보다 {loss} 느렸습니다.', '이번 랩은 최근보다 {loss} 느렸습니다.']),
    'pace_faster_by': ('race', ['최근 페이스보다 {loss} 빨랐습니다.', '이번 랩은 최근보다 {loss} 빨랐습니다. 좋습니다.']),
    # Before the corner: how much was lost there last lap and the first measured difference.
    'corner_focus_brake_early': ('coach', ['다음 코너. 지난 랩에 {loss} 잃었습니다. 제동이 일렀습니다.', '다음 코너. 지난 랩은 제동이 일렀습니다. {loss} 잃었습니다.']),
    'corner_focus_min_speed': ('coach', ['다음 코너. 지난 랩에 {loss} 잃었습니다. 최저속도가 낮았습니다.', '다음 코너. 지난 랩은 코너 속도가 낮았습니다. {loss} 잃었습니다.']),
    'corner_focus_throttle_late': ('coach', ['다음 코너. 지난 랩에 {loss} 잃었습니다. 가속이 늦었습니다.', '다음 코너. 지난 랩은 가속이 늦었습니다. {loss} 잃었습니다.']),
    'corner_focus_time': ('coach', ['다음 코너. 지난 랩에 {loss} 잃었습니다.', '다음 코너. 지난 랩 손실 구간입니다. {loss} 잃었습니다.']),
    # The same with the corner's confirmed official number or name.
    'corner_call_brake_early': ('coach', ['다음 {turn}. 지난 랩에 {loss} 잃었습니다. 제동이 일렀습니다.', '다음 {turn}. 지난 랩은 제동이 일렀습니다. {loss} 잃었습니다.']),
    'corner_call_min_speed': ('coach', ['다음 {turn}. 지난 랩에 {loss} 잃었습니다. 최저속도가 낮았습니다.', '다음 {turn}. 지난 랩은 코너 속도가 낮았습니다. {loss} 잃었습니다.']),
    'corner_call_throttle_late': ('coach', ['다음 {turn}. 지난 랩에 {loss} 잃었습니다. 가속이 늦었습니다.', '다음 {turn}. 지난 랩은 가속이 늦었습니다. {loss} 잃었습니다.']),
    'corner_call_time': ('coach', ['다음 {turn}. 지난 랩에 {loss} 잃었습니다.', '다음 {turn}. 지난 랩 손실 구간입니다. {loss} 잃었습니다.']),
    # In-car controls, from repeated measured signs over clean laps.
    'control_bias_forward': ('coach', [
        '제동 중 뒤가 흔들린 신호가 반복됩니다. 브레이크 바이어스를 앞쪽으로 한 단계 시험해 보세요.',
        '제동에서 뒤가 불안한 신호가 랩마다 나옵니다. 바이어스 앞쪽으로 한 단계.',
    ]),
    'control_tc_more': ('coach', [
        '가속 중 뒤가 미끄러진 신호가 반복됩니다. 티씨 개입을 한 단계 늘려 보세요.',
        '탈출 가속에서 뒤가 계속 미끄러집니다. 티씨 한 단계 강하게.',
    ]),
    'control_abs_more': ('coach', [
        '제동 중 뒤가 흔들리는데 에이비에스 개입은 적습니다. 에이비에스 개입을 한 단계 늘려 보세요.',
        '에이비에스 개입을 한 단계 늘려 제동 안정성을 확인해 보세요.',
    ]),
}

# The second pack goes after the first wordings of each call.
for _key, _more in radio_variations.PHRASES.items():
    PHRASES[_key] = (PHRASES[_key][0], [*PHRASES[_key][1], *_more])

# One short word or phrase each: two acknowledgements joined in one call sounded unnatural.
# '오케이.' came back as silence from the voice and was dropped.
ACKS = ('카피.', '체크.', '확인.', '알겠습니다.', '확인했습니다.', '기록했습니다.')

PLACEHOLDER = re.compile(r'\{(\w+)\}')


def build(key, seed=0, **values):
    """One wording of a call with its numbers filled in. A missing value raises; it is never guessed."""
    role, variants = PHRASES[key]
    text = PLACEHOLDER.sub(lambda match: FORMATS[match[1]](values[match[1]]), variants[seed % len(variants)])
    return {'engineer': role, 'text': text}


def catalog():
    """Every (role, sentence) the calls above can speak within the prepared ranges."""
    found = {}
    for key, (role, variants) in PHRASES.items():
        ranges = PREPARED.get(key, RANGES)
        if ranges is None:
            continue
        for template in variants:
            names = list(dict.fromkeys(PLACEHOLDER.findall(template)))
            for combination in itertools.product(*(ranges.get(name, RANGES.get(name, ())) for name in names)):
                values = dict(zip(names, combination))
                found[(role, PLACEHOLDER.sub(lambda match: FORMATS[match[1]](values[match[1]]), template))] = None
    return list(found)
