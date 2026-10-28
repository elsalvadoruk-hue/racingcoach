import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';

// Everything an installation has to be told once: where the telemetry is, the Google key and the Garage61 token.
// Secrets are sent to the local app and are never shown again.
export default function AppSettings({ apiBase, visible = true, fetcher = fetch }) {
  const [view, setView] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [folder, setFolder] = useState('');
  const [project, setProject] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const keyFile = useRef(null);

  const call = useCallback(async (path = '', method = 'GET', body, done = '') => {
    setBusy(true);
    try {
      const response = await fetcher(`${apiBase}/api/settings${path}`, body === undefined ? { method }
        : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '설정을 저장하지 못했습니다.');
      setView(data); setProject(data.settings?.gcp_project_id || ''); setError(''); setNotice(done);
      return true;
    } catch (failure) { setError(failure.message); setNotice(''); return false; } finally { setBusy(false); }
  }, [apiBase, fetcher]);
  useEffect(() => { if (visible) call(); }, [visible, call]);

  if (!view) return error ? <p role="alert" className="text-sm text-warning">{error}</p> : null;
  const added = view.settings.telemetry_dirs || [];
  const saveFolders = (folders, done) => call('', 'PUT', { telemetry_dirs: folders }, done);
  const addFolder = async () => {
    const value = folder.trim().replace(/^"|"$/g, '');
    if (!value) return;
    if (await saveFolders([...added, value], '폴더를 추가했습니다.')) setFolder('');
  };
  const readKey = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 20000) { setError('서비스 계정 키 파일이 아닙니다.'); return; }
    await call('/google-key', 'PUT', { value: await file.text() }, 'Google 키를 등록했습니다.');
  };
  const google = view.google;

  return <div className="flex flex-col gap-6" aria-label="설치 설정">
    <section className="rounded-lg border border-primary/30 bg-primary/5 p-4" data-testid="free-notice">
      <h3 className="text-sm font-semibold text-foreground">무료 프로그램</h3>
      <p className="mt-1 text-sm leading-relaxed text-foreground">이 앱은 무료이며 판매하지 않습니다. 돈을 받고 팔거나 유료로 배포하지 마세요.</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">일부 도로 지도는 비상업 조건(CC BY-NC 4.0)으로 공개된 위성사진으로 만들었습니다. 지도마다 쓰인 사진의 출처와 라이선스는 지도 파일과 함께 기록되어 있습니다.</p>
    </section>
    <section className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground">iRacing 주행 코치 {view.version ? `v${view.version}` : ''} · 데이터 폴더</h3>
      <p className="mt-2 break-all font-mono text-sm text-foreground">{view.data_dir}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">분석 기록, 음성 메모, 받아 둔 음성, Google 키와 이 설정이 모두 이 폴더에 있습니다. 폴더째 옮기거나 백업하면 됩니다.</p>
      <p className="mt-2 text-sm text-foreground">받아 둔 음성: 엔지니어 {view.audio.radio_clips.toLocaleString()}개 · 스포터 {view.audio.spotter_clips.toLocaleString()}개</p>
    </section>

    <section className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground">주행 파일(IBT) 폴더</h3>
      <ul className="mt-2 flex flex-col gap-1 text-sm">
        {view.telemetry_folders.map((item) => <li key={item.path} className="flex flex-wrap items-center justify-between gap-2">
          <span className="min-w-0 break-all font-mono text-foreground">{item.path}</span>
          <span className="flex items-center gap-2">
            <span className={item.exists ? 'text-foreground' : 'text-muted-foreground'}>{item.exists ? '사용 중' : '없음'}{item.added ? '' : ' · 기본'}</span>
            {item.added && <Button type="button" variant="outline" size="sm" disabled={busy}
              onClick={() => saveFolders(added.filter((path) => path !== item.path), '폴더를 뺐습니다.')} aria-label={`${item.path} 빼기`}>빼기</Button>}
          </span>
        </li>)}
      </ul>
      <div className="mt-3 flex flex-wrap gap-2">
        <input className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground" value={folder}
          onChange={(event) => setFolder(event.target.value)} placeholder="예: D:\iRacing\telemetry" aria-label="추가할 주행 파일 폴더" />
        <Button type="button" disabled={busy || !folder.trim()} onClick={addFolder}>폴더 추가</Button>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">iRacing 기본 위치(문서\iRacing\telemetry)는 자동으로 찾습니다. 다른 곳에 저장한다면 탐색기 주소창의 경로를 붙여 넣으세요.</p>
    </section>

    <section className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground">Google Cloud (음성 질문 · AI 답변 · 새 음성 합성)</h3>
      <p className="mt-2 text-sm text-foreground">{google.key_present
        ? `키 등록됨 · 프로젝트 ${google.key_project_id || '확인 불가'} · ${google.key_account || ''}` : '키가 등록되지 않았습니다.'}</p>
      <p className="mt-1 text-sm text-foreground">AI 연결: {google.ai_switched_off ? '꺼 둠' : google.ai_enabled ? '사용 가능' : '연결 안 됨'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input ref={keyFile} type="file" accept="application/json,.json" className="hidden" onChange={readKey} aria-label="Google 서비스 계정 키 파일" />
        <Button type="button" disabled={busy} onClick={() => keyFile.current?.click()}>{google.key_present ? '키 파일 바꾸기' : '키 파일 등록'}</Button>
        {google.key_present && <Button type="button" variant="outline" disabled={busy} onClick={() => call('/google-key', 'DELETE', undefined, 'Google 키를 지웠습니다.')}>키 지우기</Button>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="text-sm text-foreground" htmlFor="gcp-project">프로젝트 ID</label>
        <input id="gcp-project" className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground" value={project}
          onChange={(event) => setProject(event.target.value)} placeholder={google.key_project_id || '키 파일의 프로젝트를 씁니다'} />
        <Button type="button" variant="outline" disabled={busy || project === (view.settings.gcp_project_id || '')}
          onClick={() => call('', 'PUT', { gcp_project_id: project }, '프로젝트를 저장했습니다.')}>저장</Button>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">키가 없어도 주행 분석과, 이미 받아 둔 음성으로 나오는 스포터·엔지니어 안내는 그대로 됩니다. 키는 마이크 질문, AI 답변, 새 문장 합성에만 필요하고 사용한 만큼 본인 계정에 과금됩니다. 키 파일은 이 컴퓨터의 데이터 폴더에만 저장되고 화면에 다시 표시되지 않습니다.</p>
    </section>

    <section className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground">Garage61 개인 API 토큰</h3>
      <p className="mt-2 text-sm text-foreground">{view.garage61.token_present ? '토큰 등록됨' : '토큰이 등록되지 않았습니다.'}</p>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">토큰을 등록하지 않으면 오류 없이 내 주행 데이터끼리 비교합니다. Garage61을 사용하려면 driving_data 권한을 포함한 개인 API 토큰을 발급받아 저장하세요. 토큰 등록됨은 저장 상태이며 인증 성공을 뜻하지 않습니다. 등록한 토큰의 인증·권한에 문제가 있으면 오류를 안내합니다. 토큰을 저장한 뒤 비교창을 다시 열면 수집을 시도합니다. 오류가 있으면 Garage61 다시 확인으로 재시도하세요. 일반 수집에는 Playwright 설치가 필요 없습니다.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input type="password" autoComplete="off" className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground" value={token}
          onChange={(event) => setToken(event.target.value)} placeholder="토큰 붙여 넣기" aria-label="Garage61 토큰" />
        <Button type="button" disabled={busy || !token.trim()} onClick={async () => { if (await call('/garage61-token', 'PUT', { value: token }, '토큰을 저장했습니다.')) setToken(''); }}>저장</Button>
        {view.garage61.token_present && <Button type="button" variant="outline" disabled={busy} onClick={() => call('/garage61-token', 'DELETE', undefined, '토큰을 지웠습니다.')}>지우기</Button>}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">Garage61 기준 랩 비교를 쓸 때만 필요합니다.</p>
    </section>

    {notice && <p role="status" className="text-sm text-foreground">{notice}</p>}
    {error && <p role="alert" className="text-sm text-warning">{error}</p>}
  </div>;
}
