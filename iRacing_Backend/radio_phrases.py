"""Engineer radio wording: several ways to say one thing, with measured numbers.

Every call is one whole sentence group synthesized as a single clip. Joining
separately synthesized fragments sounded broken, and a number followed by a
comma ran into the next word, so a number always ends its sentence: a full
stop follows it (or a short approved ending such as "입니다."). Commas are not
used in spoken text. Only the program's measured values fill the placeholders.
"""
import itertools
import os
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
    'count': lambda v: f'{int(v)}대', 'temp': lambda v: f'{round(v)}도',
    # How many times: "두 번", never "2번" (read as a number, "이 번", like a corner).
    'times': lambda v: ('', '한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열')[int(v)] + ' 번',
    'nth': lambda v: ('', '첫', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열')[int(v)] + ' 번째',
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

RACE_MINUTES = (10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 75, 80, 90, 100, 120, 150, 160, 180, 240, 360, 480, 600, 720, 1440)

PREPARED = {
    # The formation lap (live_formation): the race laid out before the start.
    'form_length_min': {'min': RACE_MINUTES}, 'form_length_laps': {'laps': range(3, 201)},
    'form_grid': {'pos': range(1, 61)}, 'form_field': {'count': range(2, 61)},
    'form_fuel': {'liters': range(5, 151)}, 'form_pit_speed': {'kph': range(40, 131)}, 'form_pit_loss': {'sec': range(10, 91)},
    'rejoin_ahead_of': {'pos': range(1, 61)}, 'rejoin_behind': {'pos': range(1, 61)}, 'rejoin_margin': {'gap': tenths(.1, 3.0)},
    'stint_that': {'laps': range(1, 61)}, 'stint_ahead': {'laps': range(1, 61)}, 'stint_behind': {'laps': range(1, 61)},
    'stint_more': {'laps': range(3, 41)}, 'stint_less': {'laps': range(3, 41)},
    'stops_ahead': {'times': range(1, 11)}, 'stops_behind': {'times': range(1, 11)},
    'form_hist_nth': {'nth': range(2, 11)}, 'form_hist_pos': {'pos': range(1, 61)}, 'form_hist_temp_lower': {'temp': range(2, 31)}, 'form_hist_temp_higher': {'temp': range(2, 31)},
    'form_hist_loss': {'turn': [f'{n}번 코너' for n in range(1, 31)]}, 'form_hist_loss_also': {'turn': [f'{n}번 코너' for n in range(1, 31)]},
    'form_track_temp': {'temp': range(0, 66)}, 'form_air_temp': {'temp': range(-5, 46)}, 'form_incident_limit': {'points': range(4, 41)},
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
    # Where a stop now brings the car out against the one it would rejoin nearest to (2026-10-14).
    'rejoin_ahead_of': ('strategy', ['지금 피트하면 클래스 {pos} 차 바로 앞으로 나옵니다.', '지금 들어가면 클래스 {pos} 차 앞에서 나옵니다.']),
    'rejoin_behind': ('strategy', ['지금 피트하면 클래스 {pos} 차 바로 뒤로 나옵니다.', '지금 들어가면 클래스 {pos} 차 뒤에서 나옵니다.']),
    'rejoin_margin': ('strategy', ['차이는 {gap}입니다.', '간격은 {gap}입니다.']),
    # How long the cars around have been out since their last stop, against our own stint.
    'stint_that': ('strategy', ['그 차는 이번 스틴트 {laps}째입니다.', '그 차는 피트 후 {laps}째입니다.']),
    'stint_ahead': ('strategy', ['앞차는 이번 스틴트 {laps}째입니다.', '앞차는 피트 후 {laps}째입니다.']),
    'stint_behind': ('strategy', ['뒤차는 이번 스틴트 {laps}째입니다.', '뒤차는 피트 후 {laps}째입니다.']),
    'stint_more': ('strategy', ['우리보다 {laps} 더 탔습니다.', '우리보다 {laps} 깁니다.']),
    'stint_less': ('strategy', ['우리보다 {laps} 덜 탔습니다.', '우리보다 {laps} 짧습니다.']),
    'stops_ahead': ('strategy', ['앞차는 피트를 {times} 했습니다.', '앞차는 지금까지 {times} 피트했습니다.']),
    'stops_behind': ('strategy', ['뒤차는 피트를 {times} 했습니다.', '뒤차는 지금까지 {times} 피트했습니다.']),
    'stops_none_ahead': ('strategy', ['앞차는 아직 피트하지 않았습니다.', '앞차는 아직 한 번도 피트하지 않았습니다.']),
    'stops_none_behind': ('strategy', ['뒤차는 아직 피트하지 않았습니다.', '뒤차는 아직 한 번도 피트하지 않았습니다.']),
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

# The formation lap. A mark in square brackets is a pause for the voice (Chirp 3), not a word; the screen never shows it.
PHRASES.update({
    'form_hello': ('race', ['무전 연결됐습니다. [pause short] 포메이션 랩 동안 오늘 경기를 정리해 드리겠습니다.',
                            '포메이션 랩입니다. [pause short] 출발 전에 오늘 경기를 짚어 드리겠습니다.',
                            '출발 전 브리핑입니다. [pause short] 포메이션 랩 동안 천천히 들으세요.']),
    'form_length_min': ('race', ['오늘 경기는 {min}입니다.', '{min} 경기입니다.', '경기 시간은 {min}입니다.']),
    'form_length_laps': ('race', ['오늘 경기는 {laps}입니다.', '{laps} 경기입니다.', '경기 거리는 {laps}입니다.']),
    'form_grid': ('race', ['클래스 {pos}에서 출발합니다.', '출발 순위는 클래스 {pos}입니다.', '그리드는 클래스 {pos}입니다.']),
    'form_field': ('race', ['같은 클래스는 {count}입니다.', '같은 클래스에 {count}가 달립니다.', '클래스 참가 차량은 {count}입니다.']),
    # Last time at this track. The lap time has one wording (3,900 clips); the sentence before it carries the variety.
    'form_hist_first': ('race', ['이 차로는 이 트랙의 기록이 없습니다. [pause short] 오늘 기준을 만들어 봅시다.',
                                 '이 트랙은 저희 기록에 처음입니다. 첫 랩들은 여유 있게 가세요.',
                                 '이 조합은 처음 달립니다. [pause short] 오늘 기록이 기준이 됩니다.']),
    'form_hist_nth': ('race', ['이 트랙은 이번이 {nth}네요.', '여기는 이번이 {nth}죠?', '이 트랙은 이번이 {nth}입니다.']),
    # From the eleventh time on: the feeling of an old acquaintance, in many ways so it does not wear out.
    'form_hist_familiar': ('race', list(radio_variations.FAMILIAR)),
    'form_hist_best': ('race', ['이 트랙의 지난번 베스트입니다.', '지난번 이 트랙에서 가장 빨랐던 랩입니다.', '지난번 베스트 랩입니다.']),
    'form_lap': ('race', ['{lap}입니다.']),
    'form_hist_pos': ('race', ['지난번에는 클래스 {pos}로 마쳤습니다.', '지난 경기 결과는 클래스 {pos}였습니다.', '지난번 순위는 클래스 {pos}입니다.']),
    'form_hist_temp_lower': ('race', ['오늘 노면은 그때보다 {temp} 낮습니다.', '노면 온도가 지난번보다 {temp} 낮습니다.', '그때보다 노면이 {temp} 차갑습니다.']),
    'form_hist_temp_higher': ('race', ['오늘 노면은 그때보다 {temp} 높습니다.', '노면 온도가 지난번보다 {temp} 높습니다.', '그때보다 노면이 {temp} 뜨겁습니다.']),
    'form_hist_temp_same': ('race', ['노면 온도는 그때와 비슷합니다.', '오늘 노면은 지난번과 비슷합니다.', '노면 조건은 그때와 거의 같습니다.']),
    'form_hist_loss': ('race', ['지난번에는 {turn}에서 가장 많이 잃었습니다.', '지난번 가장 큰 손실은 {turn}였습니다.', '지난번에 시간을 가장 많이 잃은 곳은 {turn}입니다.']),
    'form_hist_loss_also': ('race', ['{turn}에서도 잃었습니다.', '그다음은 {turn}였습니다.', '{turn}도 손실이 컸습니다.']),
    'form_fuel': ('strategy', ['출발 연료는 {liters}입니다.', '탱크에 {liters} 있습니다.', '지금 연료는 {liters}입니다.']),
    'form_pit_speed': ('strategy', ['피트 제한속도는 시속 {kph}입니다.', '피트로드는 시속 {kph}까지입니다.', '피트 제한은 시속 {kph}입니다.']),
    'form_pit_loss': ('strategy', ['이 트랙의 피트 통과 손실은 약 {sec}입니다.', '피트를 지나는 데 약 {sec} 잃습니다.', '피트 통과에 약 {sec} 걸립니다.']),
    'form_rolling': ('spotter', ['롤링 스타트입니다. [pause short] 그린 플래그 전에는 자리를 지키세요.',
                                 '롤링 스타트입니다. 그린이 나올 때까지 추월은 없습니다.',
                                 '그린 플래그 전입니다. [pause short] 순서를 지키면서 따라가세요.']),
    'form_hold_station': ('spotter', ['앞차와 간격을 일정하게 유지하세요.', '앞차와 너무 벌어지지 않게 따라가세요.', '대열 간격을 맞춰 주세요.']),
    'form_first_corner': ('spotter', ['첫 코너는 차가 몰립니다. [pause short] 옆 차량은 제가 불러 드리겠습니다.',
                                      '출발 직후에는 좌우를 계속 불러 드리겠습니다. 공간을 남겨 두세요.',
                                      '첫 코너에서는 무리하지 마세요. [pause short] 옆에 차가 있으면 바로 알려 드립니다.']),
    'form_cold_tyres': ('coach', ['타이어가 아직 차갑습니다. [pause short] 첫 랩은 제동을 조금 일찍 시작하세요.',
                                  '타이어 온도가 올라오기 전입니다. 첫 랩은 여유를 두고 달리세요.',
                                  '차가운 타이어로 출발합니다. [pause short] 첫 두 코너는 평소보다 일찍 제동하세요.']),
    'form_cold_brakes': ('coach', ['포메이션 랩 동안 브레이크와 타이어의 열을 올리세요.', '직선에서 좌우로 흔들어 타이어를 데워 두세요.',
                                   '브레이크를 몇 번 밟아 온도를 올려 두세요.']),
    'form_track_temp': ('race', ['노면 온도는 {temp}입니다.', '지금 노면은 {temp}입니다.', '트랙 온도는 {temp}입니다.']),
    'form_air_temp': ('race', ['기온은 {temp}입니다.', '바깥 기온은 {temp}입니다.', '대기 온도는 {temp}입니다.']),
    'form_wet': ('race', ['노면이 젖어 있습니다. [pause short] 접지력이 낮으니 조심하세요.', '젖은 노면입니다. 제동 거리가 길어집니다.',
                          '트랙이 젖어 있습니다. [pause short] 마른 라인을 찾아 달리세요.']),
    'form_dry': ('race', ['노면은 말라 있습니다.', '마른 노면입니다.', '트랙은 건조합니다.']),
    'form_incident_limit': ('race', ['인시던트 한도는 {points}입니다.', '인시던트는 {points}까지입니다.', '인시던트 {points}이면 실격입니다.']),
    'form_multiclass': ('race', ['여러 클래스가 함께 달립니다. [pause short] 다른 클래스 차량은 스포터가 알려 드립니다.',
                                 '다른 클래스와 같이 달리는 경기입니다. 접근하는 차는 미리 불러 드리겠습니다.',
                                 '멀티클래스 경기입니다. [pause short] 빠른 차가 뒤에서 오면 알려 드리겠습니다.']),
    'form_ask': ('race', ['경기 중 궁금한 것은 무전 키를 누르고 물어보세요.', '간격이나 연료가 궁금하면 무전으로 물어보세요.',
                          '필요한 것이 있으면 언제든 무전 주세요.']),
    'form_calls': ('race', ['랩타임과 연료와 피트 시점은 때가 되면 먼저 알려 드리겠습니다.', '연료와 피트는 저희가 챙기겠습니다. [pause short] 주행에 집중하세요.',
                            '숫자는 저희가 보고 있겠습니다. 운전에만 집중하세요.']),
    'form_ready': ('race', ['준비됐습니다.', '저희는 준비됐습니다.', '모두 준비됐습니다.']),
    'form_good_luck': ('race', ['좋은 경기 하세요.', '깔끔하게 출발합시다.', '끝까지 함께 가겠습니다. [pause short] 좋은 경기 하세요.']),
})

# The second pack goes after the first wordings of each call.
for _key, _more in radio_variations.PHRASES.items():
    PHRASES[_key] = (PHRASES[_key][0], [*PHRASES[_key][1], *_more])

# One short word or phrase each: two acknowledgements joined in one call sounded unnatural.
# '오케이.' came back as silence from the voice and was dropped.
ACKS = ('카피.', '체크.', '확인.', '알겠습니다.', '확인했습니다.', '기록했습니다.')

PLACEHOLDER = re.compile(r'\{(\w+)\}')


# The spotter's voice can be chosen (2026-10-13). The first is the one everything was made in.
SPOTTER_VOICES = ('Fenrir', 'Algieba', 'Schedar', 'Aoede')
SPOTTER_VOICE_LABELS = {'Fenrir': '남성 · 기본', 'Algieba': '남성', 'Schedar': '남성', 'Aoede': '여성'}
VOICE_PREFIX = 'ko-KR-Chirp3-HD-'
# A wording count per call for the further voices (none now: every voice has every wording).
EXTRA_VOICE_WORDINGS = {}     # was {'lap_time_read': 2} to save space; the driver asked for all six in every voice (2026-10-13)
# Calls kept to one wording on purpose (each wording of a lap time is 3,900 clips).
SINGLE_WORDING = ('form_lap',)


def spotter_voice():
    """The short name of the spotter voice in use."""
    name = os.getenv('IRACING_TTS_SPOTTER_VOICE', '').strip().removeprefix(VOICE_PREFIX)
    return name if name in SPOTTER_VOICES else SPOTTER_VOICES[0]


def wordings(key):
    """(role, the wordings of a call that exist in the voice it is spoken in)."""
    role, variants = PHRASES[key]
    limit = EXTRA_VOICE_WORDINGS.get(key) if role == 'spotter' and spotter_voice() != SPOTTER_VOICES[0] else None
    return role, variants[:limit] if limit else variants


def build(key, seed=0, **values):
    """One wording of a call with its numbers filled in. A missing value raises; it is never guessed."""
    role, variants = wordings(key)
    text = PLACEHOLDER.sub(lambda match: FORMATS[match[1]](values[match[1]]), variants[seed % len(variants)])
    return {'engineer': role, 'text': text}


def catalog():
    """Every (role, sentence) the calls above can speak within the prepared ranges."""
    found = {}
    for key in PHRASES:
        role, variants = wordings(key)
        ranges = PREPARED.get(key, RANGES)
        if ranges is None:
            continue
        for template in variants:
            names = list(dict.fromkeys(PLACEHOLDER.findall(template)))
            for combination in itertools.product(*(ranges.get(name, RANGES.get(name, ())) for name in names)):
                values = dict(zip(names, combination))
                found[(role, PLACEHOLDER.sub(lambda match: FORMATS[match[1]](values[match[1]]), template))] = None
    return list(found)
