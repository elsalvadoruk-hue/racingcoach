"""Approved local-question recordings. Runtime reads prepared audio only.

Each tuple is (role, default wording, short wording). Wording is part of the
RadioAudioCache.path key and must remain identical to the approved recordings.
No runtime dependency on the review scripts in output/ is needed.
"""

STYLES = ('default', 'short')
LINES = {
    'remaining': ('strategy', '경기 남은 시간 말씀이신가요?', '경기 남은 시간이요?'),
    'gap': ('strategy', '앞차 간격 말씀이신가요?', '앞차 간격이요?'),
    'lap': ('strategy', '내 최근 랩타임 말씀이신가요?', '내 최근 랩타임이요?'),
    'pace': ('strategy', '완주할 연료가 충분한지 물으셨나요?', '완주 연료가 충분한지요?'),
    'rejected': ('strategy', '어떤 정보를 원하시는지 질문을 다시 말씀해 주세요.', '원하는 정보를 다시 말씀해 주세요.'),
    'corner_basis': ('coach', '이 차로 깨끗하게 통과한 구간별 최고 기록과 비교했습니다.', '이 차의 깨끗한 구간 최고 기록 기준입니다.'),
    'catch_within': ('race', '현재 추세면 경기 종료 전에 앞차에 따라붙을 것으로 예상됩니다.', '이 추세면 종료 전 앞차에 붙을 듯합니다.'),
    'catch_after': ('race', '현재 추세면 경기 종료 전에는 앞차에 따라붙기 어렵습니다.', '이 추세면 종료 전 앞차 추격이 어렵습니다.'),
    'defend_within': ('race', '현재 추세면 경기 종료 전에 뒤차가 따라붙을 것으로 예상됩니다.', '이 추세면 종료 전 뒤차가 붙을 듯합니다.'),
    'defend_after': ('race', '현재 추세면 경기 종료 전에는 뒤차가 따라붙기 어렵습니다.', '이 추세면 종료 전 뒤차가 붙기 어렵습니다.'),
    'fuel_basis': ('strategy', '남은 경기 거리와 관측한 연료 소비량으로 부족분을 계산했습니다.', '부족분은 남은 거리와 소비 기록 기준입니다.'),
    'fuel_reserve': ('strategy', '추천 급유량에는 설정한 안전 여유가 포함됩니다.', '추천량에는 설정한 안전 여유도 포함됩니다.'),
    'fuel_target_only': ('strategy', '완주 목표 소비량입니다. 실제 달성 가능 여부는 확인이 필요합니다.', '완주 소비 목표이며, 달성 여부는 미확인입니다.'),
    'fuel_stop_required': ('strategy', '연료를 절약해도 남은 필수 피트 정차는 해야 합니다.', '절약해도 필수 정차는 해야 합니다.'),
    'fuel_energy_short': ('strategy', '현재 소비량이면 가상 에너지도 완주까지 부족합니다.', '이대로면 완주할 가상 에너지도 부족합니다.'),
    'corner_repeat_all': ('coach', '최근 비교 가능한 세 랩 모두 같은 구간에서 손실이 있었습니다.', '최근 비교한 세 랩 모두 여기서 잃었습니다.'),
    'corner_repeat_some': ('coach', '최근 비교 가능한 세 랩 중 두 랩에서 같은 구간 손실이 있었습니다.', '최근 비교한 세 랩 중 두 랩은 여기서 잃었습니다.'),
    'corner_repeat_once': ('coach', '최근 비교 가능한 세 랩 중 지난 랩에서만 이 구간 손실이 있었습니다.', '최근 비교한 세 랩 중 지난 랩만 여기서 잃었습니다.'),
}
PROMPT_KEYS = frozenset(('remaining', 'gap', 'lap', 'pace', 'rejected'))
BY_WORDING = {(role, text): key for key, (role, *texts) in LINES.items() for text in texts}


def wording(key, style='default'):
    """Return the exact (role, text) pair used by the prepared cache."""
    if style not in STYLES:
        raise ValueError('Unknown local-question voice style.')
    role, normal, short = LINES[key]
    return role, short if style == 'short' else normal


def style_for(packs, role, *, detail='default'):
    """Always use short recordings for these fixed confirmations/explanations.

    Role pack selection and detail still govern other answers independently.
    The approved default recordings remain available in the explicit catalog.
    """
    return 'short'


def select(role, text, packs=None, *, detail='default'):
    key = BY_WORDING.get((role, text))
    return wording(key, style_for(packs, role, detail=detail)) if key else (role, text)


def fixed_catalog():
    """All approved (key, role, style, text) rows, without synthesis or I/O."""
    return tuple((key, role, style, normal if style == 'default' else short)
                 for style in STYLES for key, (role, normal, short) in LINES.items())
