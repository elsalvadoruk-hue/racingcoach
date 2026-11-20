"""Prepared race-coach calls shared by live playback and the listening sheet."""
# category, engineer, standard wording, short wording
CALLS = {
    'pack_close': ('race_pack', 'race', '앞에 두 대 붙어 있습니다. 다음 제동에 여유를 두세요.', '앞에 두 대 붙어 있음. 간격 유지.'),
    'pack_fight': ('race_pack', 'race', '앞에 두 대 배틀 중입니다. 다음 코너 주의하세요.', '앞에 두 대 배틀 중.'),
    'pack_slow': ('race_pack', 'race', '앞에 두 대 배틀로 느려졌습니다. 제동에 여유를 두세요.', '앞에 두 대, 배틀로 느려짐.'),
    'save_stop': ('race_coast', 'strategy', '연료 절약으로 피트 한 번 줄이는 편이 빠릅니다.', '연료 아끼면 피트 한 번 덜 감.'),
    'save_fill': ('race_coast', 'strategy', '코스팅하면 급유 시간을 줄여 결승까지 더 빠릅니다.', '코스팅하면 급유 시간 줄어듦.'),
    'coast_here': ('race_coast', 'strategy', '다음 제동 구간은 평소보다 조금 일찍 가속을 놓으세요.', '다음 코너, 액셀 일찍 놓기.'),
    'coast_half': ('race_coast', 'strategy', '다음 제동 전, 반 초 정도 먼저 가속을 놓으세요.', '다음 코너, 액셀 반 초 일찍 놓기.'),
    'coast_second': ('race_coast', 'strategy', '다음 제동 전, 일 초 정도 먼저 가속을 놓으세요.', '다음 코너, 액셀 일 초 일찍 놓기.'),
    'normal_pace': ('race_coast', 'strategy', '절약해도 피트에서 얻는 시간이 적습니다. 정상 페이스로 가세요.', '연료 아껴도 이득 적음. 평소 페이스로.'),
    'fuel_secured': ('race_coast', 'strategy', '완주 연료 확보했습니다. 정상 페이스로 돌아가세요.', '완주 연료 충분. 평소 페이스로.'),
    'cut_margin': ('race_limits', 'race', '점수 여유 있습니다. 다음 코너는 잘라서 시간을 벌었지만 트랙 이탈로 인시던트 한 점 받았습니다.', '점수 여유 있음. 다음 코너 컷 가자.'),
    'cut_penalty': ('race_limits', 'race', '다음 구간의 깊은 컷에서 페널티 신호가 있었습니다. 트랙 안쪽에 여유를 두세요.', '다음 코너, 깊게 잘랐다가 페널티 받았음.'),
    'cut_risk': ('race_limits', 'race', '점수 여유가 적습니다. 다음 구간에서 반복 이탈했습니다.', '인시던트 한도 임박. 다음 코너 주의.'),
    'cut_repeat': ('race_limits', 'race', '다음 구간에서 반복 이탈했습니다. 트랙 안쪽에 여유를 두세요.', '다음 코너는 자주 벗어나니까 살짝 여유 두고.'),
    'cut_no_gain': ('race_limits', 'race', '여기는 짧게 돌아도 탈출에서 손해입니다. 정상 라인을 쓰세요.', '다음 코너 잘라도 이득 없음. 평소 라인으로.'),
    'pit_now_gap': ('race_rejoin', 'strategy', '이번 랩에 피트가 유리합니다. 다음 랩은 복귀 대열이 더 가깝습니다.', '이번 랩 피트. 다음 랩엔 나올 때 붐빌 듯.'),
    'stay_for_gap': ('race_rejoin', 'strategy', '한 랩 더 갈 수 있습니다. 다음 랩 복귀 공간이 더 좋습니다.', '다음 랩 피트가 나올 때 덜 붐빌 듯.'),
    'energy_early': ('race_energy_plan', 'strategy', '예정 피트까지 가상 에너지가 부족합니다. 정차를 앞당기세요.', '가상 에너지 부족. 피트 앞당기기.'),
    'energy_save_plan': ('race_energy_plan', 'strategy', '가상 에너지를 절약하면 예정한 피트 랩을 유지할 수 있습니다.', '가상 에너지 아끼면 예정대로 피트.'),
    'plan_fuel_early': ('race_pit_target', 'strategy', '예정 피트까지 연료가 부족합니다. 정차를 앞당기세요.', '예정 랩까지 연료 부족. 피트 앞당기기.'),
    'plan_fuel_save': ('race_pit_target', 'strategy', '연료를 절약하면 예정한 피트 랩을 유지할 수 있습니다.', '연료 아끼면 예정대로 피트.'),
    'plan_both_early': ('race_energy_plan', 'strategy', '연료와 가상 에너지가 모두 부족합니다. 정차를 앞당기세요.', '연료도 에너지도 부족. 피트 앞당기기.'),
    'plan_both_save': ('race_energy_plan', 'strategy', '연료와 가상 에너지를 함께 절약하면 예정 피트까지 갈 수 있습니다.', '연료와 에너지 아끼면 예정대로 피트.'),
    'tire_push': ('race_tire_push', 'coach', '타이어 온도 올라왔습니다. 이제 푸시해도 됩니다.', '타이어 데워짐. 이제 푸시.'),
    'tire_rear_wait': ('race_tire_push', 'coach', '앞 타이어는 준비됐습니다. 뒤는 조금 더 데우세요.', '앞 타이어 준비. 뒤는 아직 차가움.'),
    'tire_press_ready': ('race_tire_push', 'coach', '네 바퀴 열간 압력이 개인 범위에 들어왔습니다.', '네 바퀴 공기압은 페이스 좋을 때랑 비슷함.'),
    'catch_straight': ('race_catch_zone', 'race', '빠른 클래스 접근 중입니다. 다음 직선에서 겹칠 것으로 보입니다.', '빠른 클래스, 다음 직선에서 붙을 듯.'),
    'catch_corner': ('race_catch_zone', 'race', '빠른 클래스 접근 중입니다. 코너 구간에서 겹칠 것으로 보입니다.', '빠른 클래스, 다음 코너에서 따라붙을 듯.'),
    'catch_train': ('race_catch_zone', 'race', '빠른 클래스 두 대가 코너 구간에서 따라붙을 것으로 보입니다.', '빠른 클래스 두 대, 다음 코너에서 붙을 듯.'),
    'catch_another': ('race_catch_zone', 'race', '빠른 클래스 한 대 더 접근합니다. 뒤쪽 확인하세요.', '뒤에 한 대 더. 다음 코너에서 붙을 듯.'),
}
FUEL_TARGETS = {25:'이십오', 50:'오십', 75:'칠십오', 100:'백', 150:'백오십', 200:'이백',
                250:'이백오십', 300:'삼백', 400:'사백', 500:'오백'}
for percent, spoken in enumerate(('일', '이', '삼', '사', '오', '육', '칠', '팔', '구', '십'), 1):
    CALLS[f'finish_save_{percent}'] = ('fuel_short', 'strategy',
        f'완주하려면 연료를 약 {spoken} 퍼센트 아껴야 합니다.', f'완주 목표, 연료 {spoken} 퍼센트 절약.')
for amount, spoken in FUEL_TARGETS.items():
    CALLS[f'coast_target_{amount}'] = ('race_coast', 'strategy',
        f'랩당 약 {spoken} 밀리리터 절약을 목표로 하세요.', f'랩당 약 {spoken} 밀리리터 절약.')
# One short recording for each encounter and location. The standard pack keeps
# its original recordings and only uses a line whose location is still true.
LOCATIONS = {
    'corner_0': '이 코너에서', 'corner_1': '다음 코너에서',
    'corner_2': '두 번째 코너에서', 'corner_3': '세 번째 코너에서',
    'straight_0': '이 직선에서', 'straight_1': '다음 직선에서',
    'after_1': '다음 코너 지나 직선에서', 'after_2': '두 코너 지나 직선에서',
    'after_3': '세 코너 지나 직선에서',
}
ENCOUNTER_CLIPS = {}
EXTRA_SHORTS = {}
BY_WORDING = {(role, normal): (key, key) for key, (_, role, normal, _) in CALLS.items()}
for key, (_, role, _, short) in CALLS.items():
    if key.startswith('finish_save_'):
        BY_WORDING[role, short] = (key, key)
for traffic, prefix in [('single', '빠른 클래스,'), ('train', '빠른 클래스 두 대,'),
                        ('another', '뒤에 한 대 더.')]:
    for location, place in LOCATIONS.items():
        corner = location.startswith('corner')
        key = ('catch_another' if traffic == 'another' else 'catch_train' if traffic == 'train'
               else 'catch_corner' if corner else 'catch_straight')
        verb = '따라붙을 듯.' if traffic == 'single' and corner else '붙을 듯.'
        short = f'{prefix} {place} {verb}'
        standard = key
        if not corner and traffic != 'another':
            standard = 'catch_straight' if location == 'straight_1' else None
        stem = key if short == CALLS[key][3] else f'{key}-{location}'
        if stem != key:
            EXTRA_SHORTS[stem] = (key, short)
        ENCOUNTER_CLIPS[traffic, location] = (key, short)
        BY_WORDING['race', short] = (standard, stem)


def selected_race_event(event, packs):
    short = event.get('race_short_text') if event else None
    if short and packs is not None and packs.is_builtin(packs.selections.get(event['engineer'])):
        return {**event, 'speech_text': short, 'spoken_text': short}
    return event


RACE_AUDIO_DIR = 'race-radio'
RECORDINGS = frozenset(
    [f'{key}-{style}.ogg' for key in CALLS for style in ('default', 'short')]
    + [f'{key}-short.ogg' for key in EXTRA_SHORTS]
)


def prepared_file(role, text, packs, directory):
    stems = BY_WORDING.get((role, text))
    if stems is None:
        return None
    standard_key, short_key = stems
    short = packs is not None and packs.is_builtin(packs.selections.get(role))
    key, style = (short_key, 'short') if short else (standard_key, 'default')
    if key is None:
        return None
    path = directory / RACE_AUDIO_DIR / f'{key}-{style}.ogg'
    return path if path.is_file() else None
