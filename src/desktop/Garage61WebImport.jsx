import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { requestJson } from '../analysis/analysisClient';
import { lapTime } from './sessionData';

export const GARAGE61_LAPS_URL = 'https://garage61.net/app/laps';
const NEW_SHOWN = 4;

// For drivers without an API token: find a lap on the Garage61 website in
// their own browser, export it as CSV, and bring the file in here. The app
// only reads files the driver downloaded or picked; it never drives the site.
export default function Garage61WebImport({ carName, trackName, disabled, onImported }) {
  const [downloads, setDownloads] = useState([]);
  const [folderMissing, setFolderMissing] = useState(false);
  const [busyName, setBusyName] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const fileInput = useRef(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const refresh = useCallback(async () => {
    try {
      const payload = await requestJson('/api/garage61/downloads', { timeoutMs: 15000 });
      if (!mounted.current) return;
      setFolderMissing(payload.available === false);
      setDownloads((payload.files || []).filter(row => !row.imported));
    } catch {
      // The listing is only a convenience (an older server has none yet);
      // the file picker still works, so a failed check stays quiet.
      if (mounted.current) setDownloads([]);
    }
  }, []);
  // Returning from the browser is when a new download appears.
  useEffect(() => {
    refresh();
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, [refresh]);

  async function finish(request, label) {
    setBusyName(label); setError(''); setNotice('');
    try {
      const result = await request();
      if (!mounted.current) return;
      setNotice(result.already_imported ? '이미 가져온 랩입니다. 기준 목록에서 선택했습니다.' : '가져왔습니다. 기준 목록에서 선택했습니다.');
      await onImported?.(result.name);
      refresh();
    } catch (failure) {
      if (!mounted.current) return;
      setError(failure.status === 404 && !failure.message
        ? '실행 중인 분석 서버가 이전 버전입니다. 실행.bat 창을 닫고 다시 실행하세요.'
        : `${label} 파일을 가져오지 못했습니다. ${failure.message}`);
    } finally {
      if (mounted.current) setBusyName('');
    }
  }
  const importDownload = name => finish(() => requestJson('/api/garage61/downloads/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }), timeoutMs: 120000,
  }), name);
  function importPicked(event) {
    const picked = event.target.files?.[0];
    event.target.value = '';
    if (!picked) return;
    if (!/^Garage 61 - .+\.csv$/i.test(picked.name)) {
      setNotice(''); setError('Garage61에서 내려받은 랩 CSV(파일 이름이 "Garage 61 - "로 시작)를 선택하세요.'); return;
    }
    finish(() => requestJson(`/api/garage61/import?filename=${encodeURIComponent(picked.name)}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: picked, timeoutMs: 120000,
    }), picked.name);
  }

  const target = [carName, trackName].filter(Boolean).join(' · ');
  const locked = disabled || Boolean(busyName);
  return <details className="source-picker-help garage61-web-import" data-testid="garage61-web-import">
    <summary>Garage61 웹에서 랩 가져오기 · API 토큰 없이</summary>
    <ol>
      <li>아래 버튼으로 Garage61을 열고 로그인합니다.{target && <> 차량·트랙: <strong>{target}</strong></>}</li>
      <li>비교할 랩의 메뉴에서 <strong>Export to CSV</strong>로 내려받습니다.</li>
      <li>이 창으로 돌아오면 다운로드 폴더의 새 CSV가 아래에 나타납니다. 가져오면 기준으로 선택됩니다.</li>
    </ol>
    <div className="garage61-web-import-actions">
      <Button size="sm" variant="outline" asChild><a href={GARAGE61_LAPS_URL} target="_blank" rel="noopener noreferrer">Garage61에서 랩 찾기</a></Button>
      <Button size="sm" variant="outline" disabled={locked} onClick={() => fileInput.current?.click()}>CSV 파일 직접 선택…</Button>
      <input ref={fileInput} type="file" accept=".csv,text/csv" aria-label="Garage61 CSV 파일" hidden onChange={importPicked} />
    </div>
    {downloads.length > 0 && <ul className="garage61-web-import-list" aria-label="다운로드 폴더의 Garage61 CSV">
      {downloads.slice(0, NEW_SHOWN).map(row => <li key={row.name}>
        <span>{row.driver_name || '드라이버'} · {lapTime(row.lap_time_s)} · {[row.car_name, row.track_name].filter(Boolean).join(' · ')}</span>
        <Button size="sm" disabled={locked} onClick={() => importDownload(row.name)}>{busyName === row.name ? '가져오는 중…' : '가져오기'}</Button>
      </li>)}
    </ul>}
    {!downloads.length && !folderMissing && <p>다운로드 폴더에 아직 가져오지 않은 Garage61 CSV가 없습니다.</p>}
    {folderMissing && <p>다운로드 폴더를 찾지 못했습니다. CSV 파일 직접 선택을 사용하세요.</p>}
    <p>같은 차량·트랙·레이아웃의 랩만 비교할 수 있습니다. 웹에서 받은 CSV에는 날씨·연료 같은 조건 정보가 없어 조건 비교는 확인 필요로 표시됩니다. 원본 파일은 그대로 두고 앱에 복사본을 보관합니다.</p>
    {busyName && <p role="status">Garage61 CSV를 확인하는 중…</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="loss">{error}</p>}
  </details>;
}
