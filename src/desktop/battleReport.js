import { useEffect, useState } from 'react';
import { labRequest } from './sessionData';

// One request per race file, shared by the summary banner and the battle tab.
const cache = new Map();

export function loadBattle(filename, game, signal, load = labRequest) {
  const key = `${game}:${filename}`;
  if (!cache.has(key)) {
    // Only a finished report is kept; a missing log or corners may appear on the next look.
    const request = load('battle', { filename }).then(report => { if (report?.status !== 'READY') cache.delete(key); return report; },
      error => { cache.delete(key); throw error; });
    cache.set(key, request);
  }
  const shared = cache.get(key);
  if (!signal) return shared;
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    shared.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

export function forgetBattle() { cache.clear(); }

export function useBattleReport(filename, game, enabled = true, load) {
  const [state, setState] = useState({ report: null, error: '', loading: Boolean(filename && enabled) });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!filename || !enabled) { setState({ report: null, error: '', loading: false }); return undefined; }
    const controller = new AbortController();
    setState({ report: null, error: '', loading: true });
    loadBattle(filename, game, controller.signal, load).then(report => setState({ report, error: '', loading: false }))
      .catch(error => { if (error?.name !== 'AbortError') setState({ report: null, error: error?.message || '배틀 분석을 불러오지 못했습니다.', loading: false }); });
    return () => controller.abort();
  }, [filename, game, enabled, attempt, load]);
  const retry = () => { cache.delete(`${game}:${filename}`); setAttempt(value => value + 1); };
  return { ...state, retry };
}

export const BATTLE_REASONS = {
  GAME_NOT_SUPPORTED: 'LMU 배틀 장면은 아직 준비 중입니다. 순위 변화는 볼 수 있습니다.',
  NOT_A_RACE: '레이스 주행에서만 배틀 장면을 계산합니다.',
  FILE_MISSING: '주행 파일을 찾지 못했습니다.',
  NO_GPS: '이 기록에 GPS 위치가 없어 배틀 장면을 그릴 수 없습니다.',
  NO_TRAFFIC_LOGS: '주변 차량 기록이 없습니다. 앱을 켠 채로 달린 레이스부터 배틀 장면을 계산합니다.',
  NO_MATCHING_TRAFFIC_LOG: '이 주행과 시간이 맞는 주변 차량 기록을 찾지 못했습니다. 앱을 켠 채로 달린 레이스에서만 계산합니다.',
  AMBIGUOUS_TRAFFIC_LOG: '이 주행에 맞는 주변 차량 기록이 여러 개라 하나를 고르지 않았습니다.',
  NO_TRACK_LENGTH: '트랙 길이를 알 수 없어 계산하지 않았습니다.',
  NO_CORNERS: '코너 정보가 없습니다. 코너 비교를 한 번 연 뒤 다시 확인하세요.',
  TOO_FEW_LAPS: '완주한 깨끗한 랩이 너무 적어 계산하지 않았습니다.',
};

export const OUTCOMES = { passed: '내가 추월', lost: '추월당함', held: '자리 지킴', behind: '뒤에서 따라감' };
export const VERDICTS = { ahead: '다음 코너 입구에서 내가 앞', alongside: '다음 제동 구간에서 옆에 붙음', closer: '상대에게 더 가까이', further: '앞선 거리를 더 벌림' };

export function bestAdvice(corner) {
  return corner.best && (!corner.usual || corner.best.gain_s > corner.usual.gain_s) ? corner.best : corner.usual;
}
