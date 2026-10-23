import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson } from '../analysis/analysisClient';

const POLL_MS = 500;
const SHOW_AFTER_MS = 800;   // quick loads finish before a bar would only flicker

let counter = 0;
const newJob = () => `job-${Date.now().toString(36)}-${(counter += 1)}`;

function remainingText(seconds) {
  if (!Number.isFinite(seconds) || seconds < 3) return '';
  if (seconds < 60) return `약 ${Math.ceil(seconds / 5) * 5}초 남음`;
  return `약 ${Math.round(seconds / 60)}분 남음`;
}

// One analysis request at a time: begin() names the job sent as progress_id, end() stops polling.
export function useAnalysisProgress() {
  const [view, setView] = useState(null);
  const job = useRef(null);
  const timer = useRef(null);
  const controller = useRef(null);
  const stop = useCallback(id => {
    if (id && job.current !== id) return;
    clearTimeout(timer.current);
    job.current = null;
    controller.current?.abort();
    setView(null);
  }, []);
  const begin = useCallback(() => {
    clearTimeout(timer.current);
    controller.current?.abort();
    const id = newJob();
    job.current = id;
    setView(null);
    const started = Date.now();
    const poll = async () => {
      if (job.current !== id) return;
      const request = new AbortController();
      controller.current = request;
      try {
        const status = await requestJson(`/api/workspace/progress/${encodeURIComponent(id)}`, { signal: request.signal, timeoutMs: 5000 });
        if (job.current === id && Date.now() - started >= SHOW_AFTER_MS) {
          if (Number.isFinite(status?.percent)) setView(status);
          else if (status?.stage === 'unknown') setView({ percent: null, label: '분석 준비 중' });
        }
      } catch (_) { /* the bar is optional; the request itself reports failures */ }
      if (job.current === id) timer.current = setTimeout(poll, POLL_MS);
    };
    timer.current = setTimeout(poll, Math.min(POLL_MS, SHOW_AFTER_MS));
    return id;
  }, []);
  useEffect(() => () => {
    job.current = null;
    clearTimeout(timer.current);
    controller.current?.abort();
  }, []);
  return { view, begin, end: stop };
}

export function AnalysisProgressBar({ view }) {
  if (!view) return null;
  const percent = Number.isFinite(view.percent) ? Math.max(0, Math.min(100, view.percent)) : null;
  const left = remainingText(view.remaining_s);
  return <div className="analysis-progress" data-testid="analysis-progress">
    <div className="analysis-progress-label"><span>{view.label || '분석 중'}</span><span className="numeric">{percent == null ? '준비 중' : `${view.stage === 'done' ? '' : '예상 '}${percent}%`}{left ? ` · ${left}` : ''}</span></div>
    <div className="analysis-progress-track" role="progressbar" aria-label="주행 데이터 분석 예상 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
      <div className={`analysis-progress-fill${percent == null ? ' analysis-progress-waiting' : ''}`} style={percent == null ? undefined : { width: `${percent}%` }} />
    </div>
    <small>파일과 컴퓨터 상태에 따라 실제 남은 시간은 달라질 수 있습니다.</small>
  </div>;
}
