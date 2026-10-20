"""Observed section priorities for the shared driving-summary presentation.

This selects measured differences to recheck, never causes or predicted gains.
Input practice is only saved after selecting 3–6 distinct measured laps.
"""
import math


MINIMUM_SECTION_DELTA_S = .1


def driving_summary(corners):
    candidates = [row for row in corners if isinstance(row.get('delta_s'), (int, float))
                  and not isinstance(row['delta_s'], bool) and math.isfinite(row['delta_s'])
                  and row['delta_s'] >= MINIMUM_SECTION_DELTA_S]
    candidates.sort(key=lambda row: (-row['delta_s'], row.get('entry_pct', 0)))
    options = []
    for row in candidates[:3]:
        label = row.get('label') or row['id']
        comparable = all(row.get(role, {}).get('inputs_usable') is True
                         for role in ('target', 'reference'))
        action = ('이 구간의 제동·가속 위치와 진입·탈출 속도를 함께 비교하고 차이가 반복되는지 확인하세요.'
                  if comparable else '이 구간의 속도와 통과 시간을 다시 확인하세요. 페달 처리 단계가 달라 입력 차이는 비교하지 않습니다.')
        criteria = ['같은 차량·트랙에서 측정 가능한 랩 3개 이상 확보',
                    '입력 위치 편차와 구간 시간을 별도로 비교']
        plan = {
            'mode': 'DRIVING_FOCUS', 'mode_label': '측정된 구간 차이 재확인',
            'required_measured_laps': 3, 'recommended_measured_laps': 6,
            'required_clean_corners': 0,
            'lock': ['차량·트랙·레이아웃을 유지', '셋업과 주행 조건 변경을 기록'],
            'success_criteria': criteria,
            'after_run': '기록에서 기준 3~6랩을 저장하고 다음 주행의 새 랩으로 차이가 반복되는지 확인하세요.',
        }
        options.append({'focus': {
            'root_cause': 'LMU_OBSERVED_SECTION_'+row['id'],
            'corner_id': row['id'], 'root_cause_label': label+' · 시간 차이 재확인',
            'total_observed_loss_s': row['delta_s'], 'action': action,
            'success_criteria': criteria,
            'evidence_kind': 'OBSERVATION_ONLY', 'causal_effect': 'UNVERIFIED',
        }, 'corner_ids': [row['id']], 'next_run': plan})
    fallback = {
        'mode': 'MEASURE_MORE', 'mode_label': '비교 기준과 반복 주행 확인',
        'required_measured_laps': 3, 'recommended_measured_laps': 6,
        'required_clean_corners': 0,
        'title': '비교 기준을 선택하고 반복 주행을 확인하세요.',
        'primary_instruction': '현재 비교에서 0.1초 이상 느린 구간을 확인하지 못했습니다. 다른 기준 랩을 선택하거나 반복 주행을 모으세요.',
        'lock': ['같은 차량·트랙·레이아웃으로 비교', '셋업과 주행 조건 변경을 기록'],
        'success_criteria': ['측정 가능한 서로 다른 랩 3개 이상 확보', '같은 입력 처리 단계에서 반복성을 확인'],
        'after_run': '기록에서 여러 측정 랩으로 연습 기준을 저장할 수 있습니다.',
    }
    return {'provider': 'LMU', 'status': 'OBSERVATION_ONLY',
            'selection_basis': 'SECTION_DURATION_DELTA',
            'minimum_section_delta_s': MINIMUM_SECTION_DELTA_S,
            'options': options, 'next_run': options[0]['next_run'] if options else fallback}
