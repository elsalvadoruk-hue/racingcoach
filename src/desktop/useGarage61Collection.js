import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from '../analysis/analysisClient';

const POLL_MS = 5000;
const TERMINAL = new Set(['COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED', 'SKIPPED_CACHE_SUFFICIENT', 'SKIPPED_COOLDOWN']);

// A picker can preview another recording without changing the active analysis.
// Follow that recording's collection independently and leave started workers
// running when the picker closes; only this view's requests and timers stop.
export function useGarage61Collection({ filename, enabled, onCollected }) {
  const [state, setState] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const collected = useRef(onCollected);
  collected.current = onCollected;
  const retry = useCallback(() => setAttempt(value => value + 1), []);

  useEffect(() => {
    if (!enabled || !filename) { setState(null); return undefined; }
    const controller = new AbortController();
    let timer;
    let watchedJob = null;
    let requested = false;
    const live = () => !controller.signal.aborted;
    const show = (phase, payload, message) => {
      if (live()) setState({ filename, phase, health: payload?.health, job: payload?.job, message });
    };
    const matches = payload => {
      const job = payload?.job;
      const context = payload?.health?.context?.context_key;
      return Boolean(job && ((job.context_key && context) ? job.context_key === context : job.filename === filename));
    };
    const finish = payload => {
      const job = payload?.job;
      const phase = job?.state;
      const messages = {
        COMPLETE: 'Garage61 자료 수집을 마쳤습니다.',
        PARTIAL: 'Garage61 자료를 일부 받았습니다. 내려받은 기준을 확인하세요.',
        CANCELLED: 'Garage61 자료 수집이 취소됐습니다.',
        SKIPPED_CACHE_SUFFICIENT: '최신 Garage61 기준이 충분해 내려받은 자료를 재사용합니다.',
      };
      show(phase || 'FAILED', payload, messages[phase] || job?.message || (phase === 'SKIPPED_COOLDOWN'
        ? '동일 조건을 최근에 수집하여 재수집 제한 시간 동안 다운로드를 생략했습니다.'
        : 'Garage61 수집 작업을 시작하지 못했습니다. 다시 확인해 주세요.'));
      if (live() && ['COMPLETE', 'PARTIAL'].includes(phase)) collected.current?.(filename);
    };
    const schedule = () => { if (live()) timer = setTimeout(check, POLL_MS); };
    const follow = payload => {
      if (matches(payload)) {
        if (!payload.job.busy && TERMINAL.has(payload.job.state)) { finish(payload); return; }
        watchedJob = payload.job.job_id;
        show('COLLECTING', payload, payload.job.message || '선택한 주행의 Garage61 자료를 내려받고 있습니다.');
      } else {
        requested = false;
        show('WAITING', payload, '다른 주행의 자료를 수집 중입니다. 끝나면 선택한 주행의 다운로드를 시작합니다.');
      }
      schedule();
    };
    const check = async () => {
      try {
        const payload = await requestJson(`/api/garage61/collector/status?filename=${encodeURIComponent(filename)}`, { signal: controller.signal });
        if (!live()) return;
        if (matches(payload) && payload.job.busy) { follow(payload); return; }
        if (watchedJob && matches(payload) && payload.job.job_id === watchedJob && TERMINAL.has(payload.job.state)) {
          finish(payload); return;
        }
        if (!payload?.health) { show('FAILED', payload, 'Garage61 수집 상태를 확인할 수 없습니다. 분석 서버를 확인한 뒤 다시 시도하세요.'); return; }
        if (!payload.health.enabled) { show('DISABLED', payload, 'Garage61 자동 수집이 꺼져 있어 다운로드를 시작하지 않았습니다. 설정의 Garage61 기준·수집을 확인하세요.'); return; }
        if (!payload.health.needs_collection) {
          show('CACHED', payload, '최신 Garage61 기준이 충분해 내려받은 자료를 재사용합니다.');
          if (watchedJob) collected.current?.(filename);
          return;
        }
        if (payload.job?.busy) { follow(payload); return; }
        if (requested) { show('FAILED', payload, '시작한 Garage61 수집 상태를 찾을 수 없습니다. 다시 확인해 주세요.'); return; }
        requested = true;
        show('STARTING', payload, '선택한 주행의 Garage61 다운로드를 시작하고 있습니다.');
        const started = await requestJson('/api/garage61/collector/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
          body: JSON.stringify({ filename, force: false, target_count: 8, headless: false, collector_mode: 'AUTO' }),
        });
        if (!live()) return;
        if (!started?.job?.busy) { finish(started); return; }
        follow(started);
      } catch (error) {
        if (live()) show('FAILED', null, `Garage61 자료를 받지 못했습니다. ${error.message}`);
      }
    };
    show('CHECKING', null, '선택한 주행에 필요한 Garage61 자료와 다운로드 상태를 확인하고 있습니다.');
    void check();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [filename, enabled, attempt]);

  return { ...(state?.filename === filename && enabled ? state : {}), retry };
}
