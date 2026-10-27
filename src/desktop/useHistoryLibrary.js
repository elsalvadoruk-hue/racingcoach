import { useEffect, useRef, useState } from 'react';
import { readReportJson } from './driverReportRequest';

const ACTIVE = new Set(['SCANNING', 'RUNNING', 'WAITING', 'PAUSING']);
const STATES = new Set(['IDLE', ...ACTIVE, 'PAUSED', 'COMPLETE', 'FAILED']);
export const HISTORY_LIBRARY_POLL_MS = 2500;
export const HISTORY_LIBRARY_WAIT_MS = 5000;
const count = value => Number.isFinite(value) && value >= 0 ? value : null;

export default function useHistoryLibrary(base, game, onRevision) {
  const scope = `${base}|${game}`;
  const [state, setState] = useState({ key: scope, job: null, busy: false, error: '', waiting: false });
  const current = useRef(null), callback = useRef(onRevision);
  callback.current = onRevision;
  useEffect(() => {
    const run = { active: true, busy: false, controller: null, timer: null, notifyTimer: null,
      job: null, revision: null, lastNotify: 0, autoStarted: false, pendingStart: false, waiting: false };
    current.current = run;
    setState({ key: scope, job: null, busy: false, error: '', waiting: false });
    const notify = force => {
      clearTimeout(run.notifyTimer);
      const delay = force ? 0 : Math.max(0, HISTORY_LIBRARY_POLL_MS - (Date.now() - run.lastNotify));
      const emit = () => { if (run.active) { run.lastNotify = Date.now(); callback.current?.(); } };
      if (delay) run.notifyTimer = setTimeout(emit, delay); else emit();
    };
    const poll = waiting => { run.timer = setTimeout(() => run.execute('status'), waiting ? HISTORY_LIBRARY_WAIT_MS : HISTORY_LIBRARY_POLL_MS); };
    run.execute = async (action = 'status') => {
      if (!run.active || run.busy) return;
      run.busy = true; clearTimeout(run.timer);
      run.controller = new AbortController();
      setState(previous => ({ ...previous, key: scope, busy: true, error: '' }));
      let nextAction;
      try {
        const payload = await readReportJson(`${base}/api/driver-history/library${action === 'pause' ? '/pause' : ''}?game=${game}`,
          run.controller, action === 'status' ? undefined : 'POST');
        if (!run.active || run.controller.signal.aborted) return;
        if (payload?.game !== game || !STATES.has(payload?.status)) throw new Error('과거 기록 가져오기 상태를 읽지 못했습니다. 다시 확인해 주세요.');
        const job = { status: payload.status, job_id: typeof payload.job_id === 'string' ? payload.job_id : '',
          message: typeof payload.message === 'string' ? payload.message : '',
          wait_reason: ['DRIVING', 'ANALYZING', 'COOLDOWN', 'RESOURCE_BUSY'].includes(payload.wait_reason) ? payload.wait_reason : '',
          ...Object.fromEntries(['total', 'processed', 'updated', 'skipped', 'failed', 'revision'].map(key => [key, count(payload[key])])) };
        const previous = run.job;
        run.job = job;
        const blocked = payload.busy_game && payload.busy_game !== game;
        const wantsStart = !ACTIVE.has(job.status) && (run.pendingStart || (job.status === 'IDLE' && !run.autoStarted));
        if (wantsStart && blocked) run.pendingStart = true;
        if (ACTIVE.has(job.status)) run.pendingStart = false;
        run.waiting = !!(wantsStart && blocked);
        setState({ key: scope, job, busy: false, error: '', waiting: run.waiting });
        if (run.revision !== null && job.revision !== null && job.revision !== run.revision) notify(!ACTIVE.has(job.status));
        else if (previous && ACTIVE.has(previous.status) && !ACTIVE.has(job.status)) notify(true);
        run.revision = job.revision;
        if (wantsStart && !blocked) {
          run.autoStarted = true; run.pendingStart = false; nextAction = 'start';
        } else if (ACTIVE.has(job.status)) {
          run.pendingStart = false; run.waiting = false; poll(false);
        } else if (run.waiting) poll(true);
      } catch (error) {
        if (!run.active || error?.name === 'AbortError') return;
        if (error?.status === 409 && action === 'start') {
          run.pendingStart = true; run.waiting = true;
          setState(previous => ({ ...previous, key: scope, busy: false, waiting: true, error: '' }));
          poll(true);
        } else {
          setState(previous => ({ ...previous, key: scope, busy: false,
            error: error?.name === 'TypeError' ? '기록 가져오기 상태를 확인하지 못했습니다. 연결 상태를 확인해 주세요.' : error?.message || '기록 가져오기 상태를 확인하지 못했습니다.' }));
        }
      } finally {
        run.busy = false;
        if (nextAction && run.active) void run.execute(nextAction);
      }
    };
    void run.execute();
    return () => { run.active = false; clearTimeout(run.timer); clearTimeout(run.notifyTimer); run.controller?.abort(); };
  }, [base, game, scope]);
  const visible = state.key === scope ? state : { job: null, busy: false, error: '', waiting: false };
  return { ...visible, start: () => current.current?.execute('start'), pause: () => current.current?.execute('pause'),
    retry: () => current.current?.execute('status') };
}
