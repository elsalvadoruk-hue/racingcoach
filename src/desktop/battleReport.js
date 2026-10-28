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
export const SIM_REASONS = {
  INSUFFICIENT_DYNAMICS: '페달과 속도 변화 자료가 부족해 차량 반응을 계산하지 못했습니다.',
  DISCONTINUOUS_SCENE: '연속된 1초 이상의 배틀 기록이 필요합니다.',
  SHORT_RIVAL_HISTORY: '계산 시작 전 상대의 속도 변화를 확인할 기록이 부족합니다.',
};
export const RESPONSES = { track_pace: '코너 속도에 맞춰 달림', brakes: '상대가 더 감속함', holds_speed: '상대가 현재 속도를 유지함' };
export const SIM_STATUS = { OVERLAP_POSSIBLE: '차량 겹침 가능 · 우열 보류', MODEL_UNCERTAIN: '오차가 커 우열 보류', EFFECT_WITHIN_ERROR: '차이가 오차보다 작음 · 우열 보류', CONDITIONAL: '가정에 따른 비교 · 우열 보류' };
