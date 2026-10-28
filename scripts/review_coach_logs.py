"""받은 ZIP/폴더를 오프라인 검수하고 기록 재생 HTML을 만듭니다.

    python scripts/review_coach_logs.py 받은로그폴더 --out 검수결과폴더
    python scripts/review_coach_logs.py coach-log-example.zip --out 검수결과폴더

게임·오디오·개인 설정을 열지 않으며 외부로 전송하지 않습니다.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import lzma
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
import log_pack  # noqa: E402
import oval_review  # noqa: E402
import session_log  # noqa: E402

LABELS = {'ASPHALT_OVAL': '포장 오벌', 'DIRT_OVAL': '더트 오벌', 'ROAD': '로드', 'UNKNOWN': '미확인'}


def load(path):
    data = path.read_bytes()
    manifest, radio, _ = session_log.read(data)
    # Do not decompress unrelated own-drive/handling recordings for this review.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        traffic = log_pack.unpack(archive.read('traffic.packed.xz')) if 'traffic.packed.xz' in archive.namelist() else ({}, [])
        shadow = json.loads(archive.read('banter-shadow.json')) if 'banter-shadow.json' in archive.namelist() else []
        procedures = json.loads(archive.read('oval-procedures.json')) if 'oval-procedures.json' in archive.namelist() else []
    if not isinstance(shadow, list) or not all(isinstance(row, dict) for row in shadow):
        raise ValueError('위트 무음 관측 기록 형식을 확인할 수 없습니다.')
    if any(row.get('run_id') != manifest.get('run_id') for row in shadow):
        raise ValueError('위트 무음 관측과 기본 로그의 run_id가 다릅니다.')
    if not isinstance(procedures,list) or not all(isinstance(r,dict) and r.get('run_id') == manifest.get('run_id') for r in procedures):
        raise ValueError('오벌 절차 관측 형식 또는 run_id가 기본 로그와 다릅니다.')
    if (not isinstance(traffic[0], dict) or not isinstance(traffic[1], list)
            or not all(isinstance(row, dict) for row in traffic[1])):
        raise ValueError('주변 차량 기록 형식을 확인할 수 없습니다.')
    if traffic[0].get('run_id') and manifest.get('run_id') and traffic[0]['run_id'] != manifest['run_id']:
        raise ValueError('주변 차량 기록과 기본 로그의 run_id가 다릅니다.')
    return data, manifest, radio, traffic, shadow, procedures


def markdown(result):
    cov = result['coverage']
    escape = lambda v: html.escape(str(v)).replace('|', '\\|').replace('\n', ' ')
    lines = [f"# {LABELS.get(result['profile'], '미확인')} 로그 검수", '',
             f"{escape(result.get('track'))} · {escape(result.get('car'))} · {escape(result.get('session_type'))}", '',
             f"상태: **{result['verdict']}**. 자동 검수 결과만으로 정상 판정을 내리지 않습니다.", '',
             f"수집 버전: {escape(json.dumps(result['collection_versions'], ensure_ascii=False))}",
             f"내보낸 앱 버전: {escape(result.get('export_app_version'))}", '',
             f"주변 차량 {cov['rows']:,}행 / 연속 구간 {cov['segments']:,}개. "
             f"무전 {cov['radio_rows']:,}행 중 시각 대응 {cov['aligned_radio_rows']:,}행, 대응 보류 {cov['unaligned_radio_rows']:,}행.", '',
             f"축소 정보: {escape(json.dumps(cov['selection'], ensure_ascii=False))}", '',
             f"무전 이력 집계: {escape(json.dumps(result['radio_counts'], ensure_ascii=False))}", '',
             '[기록 재생 열기](replay.html) · [전체 검수 자료](review.json)', '',
             '| 검토 항목 | 세션 시각(초) | 이유 |', '| --- | ---: | --- |']
    for finding in result['findings']:
        lines.append(f"| {escape(finding['kind'])} | {escape(finding.get('t'))} | {escape(finding.get('reason'))} |")
    if not result['findings']:
        lines.append('| 검토 신호 없음 | — | 오류 없음 또는 검증 통과를 뜻하지 않습니다. |')
    lines += ['', f"과거 로그의 위트 검토 후보 {len(result['banter_candidates'])}개는 모두 DEFERRED입니다.",
              f"절차 무음 관측 {cov['procedure_rows']}개 · 시각 대응 보류 {cov['unaligned_procedure_rows']}개.",
              '[사건 전후 회귀 자료](regression.json) · 기능별 미관측 항목은 review.json의 feature_coverage에서 확인합니다.',
              f"개발본의 무음 평가 {len(result['banter_observations'])}개. 가상 선택이며 실제 발화가 아닙니다.",
              f"관측 시각 대응 보류 {cov['unaligned_banter_rows']}개. 전체 내용은 review.json에서 확인합니다.", '',
              *['- ' + text for text in result['limitations']], '']
    return '\n'.join(lines)


def write_review(folder, result, frames):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (folder / 'report.md').write_text(markdown(result), encoding='utf-8')
    (folder / 'regression.json').write_text(json.dumps(oval_review.regression_windows(result,frames), ensure_ascii=False, allow_nan=False), encoding='utf-8')
    with (folder / 'replay.jsonl').open('w', encoding='utf-8') as handle:
        for frame in frames:
            handle.write(json.dumps(frame, ensure_ascii=False, allow_nan=False) + '\n')
    template = Path(__file__).with_name('coach_log_replay.html').read_text(encoding='utf-8')
    payload = json.dumps({'review': result, 'frames': frames}, ensure_ascii=False, allow_nan=False)
    # JSON in an inert script tag still needs protection from closing-tag injection.
    payload = payload.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    (folder / 'replay.html').write_text(template.replace('__COACH_REPLAY_DATA__', payload), encoding='utf-8')


def run(paths, output):
    files = sorted({p.resolve() for source in paths for p in (source.rglob('*.zip') if source.is_dir() else [source])})
    chosen, duplicates, errors, hashes = {}, [], [], set()
    for path in files:
        try:
            data, manifest, radio, (header, rows), shadow, procedures = load(path)
            digest = hashlib.sha256(data).hexdigest()
            if digest in hashes:
                duplicates.append({'file': path.name, 'reason': 'same_zip'})
                continue
            hashes.add(digest)
            # Same run export variants are alternatives, never counted as new drives.
            key = (manifest.get('game'), manifest.get('run_id'), manifest.get('started_at')) if manifest.get('run_id') else ('sha256', digest)
            score = (len(rows), len(radio), len(shadow), len(procedures))
            candidate = (score, digest, path)
            if key in chosen:
                old = chosen[key]
                dropped = old if score > old[0] else candidate
                duplicates.append({'file': dropped[2].name, 'reason': 'same_run_export',
                                   'note': '행 수가 많은 자료를 우선 사용. 서로 다른 내보내기는 합치지 않음'})
                if score <= old[0]:
                    continue
            chosen[key] = candidate
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, EOFError, lzma.LZMAError, zipfile.BadZipFile) as exc:
            errors.append({'file': path.name, 'reason': str(exc)[:240]})
    output.mkdir(parents=True, exist_ok=True)
    items = []
    # Hold only one decoded run at a time, even for hundreds of received ZIPs.
    for _, digest, path in chosen.values():
        try:
            data, manifest, radio, (header, rows), shadow, procedures = load(path)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('검수 중 원본 ZIP이 바뀌었습니다. 다시 실행해 주세요.')
            result, frames = oval_review.review(manifest, header, rows, radio, shadow, procedures)
            result['source'] = {'file': path.name, 'sha256': digest}
            name = 'run-' + digest[:16]
            write_review(output / name, result, frames)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, EOFError, lzma.LZMAError, zipfile.BadZipFile) as exc:
            errors.append({'file': path.name, 'reason': str(exc)[:240]})
            continue
        items.append({'folder': name, 'run_id': result['run_id'], 'profile': result['profile'],
                      'rows': result['coverage']['rows'], 'findings': len(result['findings']), 'verdict': result['verdict'],
                      'feature_coverage':result['feature_coverage']})
    index = {'version': oval_review.VERSION, 'files': len(files), 'runs': items, 'duplicates': duplicates, 'errors': errors}
    coverage = {}
    for item in items:
        if item['profile'] != 'ASPHALT_OVAL':
            continue
        for name, feature in item['feature_coverage'].items():
            total = coverage.setdefault(name, {'runs':0,'observed_runs':0,'observations':0,'mode':feature['mode'],
                                               'verdict':'MANUAL_REVIEW_REQUIRED'})
            total['runs'] += 1
            total['observed_runs'] += int(feature['observations'] > 0)
            total['observations'] += feature['observations']
    (output / 'coverage.json').write_text(json.dumps({'features':coverage, 'automatic_activation':False,
        'unit':'distinct exported runs, not drivers or races', 'errors':len(errors)}, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 받은 로그 검수', '', f'ZIP {len(files)}개 · 중복 제외 기록 {len(items)}개 · 중복 {len(duplicates)}개 · 읽기 실패 {len(errors)}개', '',
             '| 분류 | 관측 행 | 검토 신호 | 결과 |', '| --- | ---: | ---: | --- |']
    lines += [f"| {LABELS.get(i['profile'], '미확인')} | {i['rows']} | {i['findings']} | [보고서]({i['folder']}/report.md) · [재생]({i['folder']}/replay.html) |" for i in items]
    lines += ['', '[포장 오벌 기능별 누적 관측](coverage.json). 사람 수·경기 수 또는 통과율로 해석하지 않습니다.',
              '중복·읽기 실패의 세부 내용은 index.json에 남습니다. 결과는 자동 정상 판정이 아닙니다.', '']
    (output / 'index.md').write_text('\n'.join(lines), encoding='utf-8')
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('inputs', nargs='+', type=Path, help='ZIP 파일 또는 ZIP이 든 폴더')
    parser.add_argument('--out', type=Path, required=True, help='검수 결과를 저장할 폴더')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    index = run(args.inputs, args.out)
    print(f"검수 기록 {len(index['runs'])}개 · 중복 {len(index['duplicates'])}개 · 읽기 실패 {len(index['errors'])}개")
    print(f"결과: {args.out / 'index.md'}")
    return 1 if index['errors'] or not index['runs'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
