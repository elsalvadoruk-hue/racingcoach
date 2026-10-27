import { useEffect, useRef, useState } from 'react';
import { readReportJson } from './driverReportRequest';

const RESPONSES = new Set(['COMFORTABLE', 'NEUTRAL', 'DIFFICULT', 'UNSURE']);
const rows = value => Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
const validId = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
function normalize(payload, game) {
  if (payload?.game !== game || !Array.isArray(payload?.drives)) throw new Error('이 게임의 체감·도전 기록을 확인하지 못했습니다. 다시 확인해 주세요.');
  return { ...payload, drives: rows(payload.drives).filter(row => validId(row.drive_id) && row.game === game),
    selected_drive_id: validId(payload.selected_drive_id) ? payload.selected_drive_id : null,
    feeling: RESPONSES.has(payload.feeling?.response) ? payload.feeling : null,
    candidates: rows(payload.candidates).filter(row => validId(row.candidate_id)),
    active_challenge: payload.active_challenge?.status === 'ACTIVE' ? payload.active_challenge : null,
    challenge_history: rows(payload.challenge_history),
  };
}

export default function useDriverJournal(base, game, revision = 0, fixedDriveId = '') {
  const scope = `${base}|${game}|${fixedDriveId}`;
  const [selection, setSelection] = useState({scope, id:fixedDriveId});
  const selectedId = fixedDriveId || (selection.scope === scope ? selection.id : '');
  const key = `${scope}|${selectedId}`;
  const [state, setState] = useState({key, data:null, busy:true, error:'', message:''});
  const [retry, setRetry] = useState(0);
  const current = useRef(null);
  const latestRevision = useRef(revision);
  latestRevision.current = revision;
  useEffect(() => {
    const run = {active:true, controller:new AbortController(), busy:true, key,
      revision:latestRevision.current, mutating:false, pendingRefresh:false};
    current.current = run;
    setState(previous => ({key, data:previous.key === key ? previous.data : null, busy:true, error:'', message:''}));
    const url = `${base}/api/driver-journal?game=${game}${validId(selectedId) ? `&drive_id=${selectedId}` : ''}`;
    readReportJson(url, run.controller).then(payload => {
      const data = normalize(payload, game);
      if (fixedDriveId && data.selected_drive_id !== fixedDriveId) throw new Error('선택한 주행의 기록을 확인하지 못했습니다.');
      if (run.active) setState({key, data, busy:false, error:'', message:''});
    }).catch(error => {
      if (run.active && error.name !== 'AbortError') setState(previous => ({...previous, key, busy:false, error:error.message || '기록을 읽지 못했습니다.'}));
    }).finally(() => { run.busy = false; });
    return () => { run.active = false; run.controller.abort(); };
  }, [base, game, key, selectedId, fixedDriveId, retry]);

  useEffect(() => {
    const run=current.current;
    if (!run?.active || run.revision===revision) return;
    run.revision=revision;
    // A library update may arrive while SQLite is saving. Read the committed
    // state after that mutation instead of cancelling it and racing its write.
    if (run.mutating) run.pendingRefresh=true;
    else setRetry(value=>value+1);
  },[revision]);

  async function mutate(path, method, body, message) {
    const run = current.current;
    if (!run?.active || run.busy) return;
    run.busy = true;
    run.mutating = true;
    run.controller = new AbortController();
    setState(previous => ({...previous, busy:true, error:'', message:''}));
    try {
      const selected = state.key === key ? state.data?.selected_drive_id : null;
      const payload = await readReportJson(`${base}/api/driver-journal${path}?game=${game}${validId(selected) ? `&drive_id=${selected}` : ''}`, run.controller, method, body);
      const data = normalize(payload, game);
      if (fixedDriveId && data.selected_drive_id !== fixedDriveId) throw new Error('선택한 주행의 저장 결과를 확인하지 못했습니다. 다시 확인해 주세요.');
      if (run.active) setState({key:run.key, data, busy:false, error:'', message});
    } catch (error) {
      if (run.active && error.name !== 'AbortError') setState(previous => ({...previous, busy:false, error:error.message || '기록을 저장하지 못했습니다. 다시 시도해 주세요.'}));
    } finally {
      run.busy = false;
      run.mutating = false;
      if(run.active&&run.pendingRefresh)setRetry(value=>value+1);
    }
  }
  const visible = state.key === key ? state : {data:null, busy:true, error:'', message:''};
  const driveId = visible.data?.selected_drive_id;
  return { ...visible, selectDrive:id => { if (!fixedDriveId && validId(id)) setSelection({scope,id}); },
    retry:() => setRetry(value=>value+1),
    saveFeeling:(response,difficulty=null) => validId(driveId) && mutate(`/feelings/${driveId}`,'PUT',{response,difficulty},'체감을 저장했습니다.'),
    deleteFeeling:() => validId(driveId) && mutate(`/feelings/${driveId}`,'DELETE',undefined,'체감 기록을 지웠습니다.'),
    chooseChallenge:candidate => mutate('/challenges','POST',{candidate_id:candidate.candidate_id,drive_id:candidate.drive_id},'도전을 선택했습니다.'),
    cancelChallenge:id => mutate(`/challenges/${id}/cancel`,'POST',undefined,'도전을 취소했습니다.'),
    reconcile:() => mutate('/reconcile','POST',undefined,'새 주행으로 달성 여부를 확인했습니다.'),
  };
}
