import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import LiveCoach from '../components/LiveCoach';
import { controlsFromHost, workspaceFromHash } from './labControlState';
import { listAudioDevices } from './labAudioDevices';
import { voiceCapability } from './labVoiceCapability';

const RuntimeContext = createContext(null);

export function useLabControls() {
  const runtime = useContext(RuntimeContext);
  if (!runtime) throw new Error('DesktopRuntimeProvider is required.');
  return runtime;
}

function RuntimeBridge({ host, children }) {
  const { apiBase } = host;
  const [overlay, setOverlay] = useState(null), [overlayBusy, setOverlayBusy] = useState(false);
  const [devices, setDevices] = useState([]), [message, setMessage] = useState(''), [deviceBusy, setDeviceBusy] = useState(false);
  const refreshOutputs = host.audioOutput.refresh;
  const mounted = useRef(false), permissionFlight = useRef(null), generation = useRef(0), overlayMutation = useRef(false);
  const requestOverlay = useCallback(async (enabled) => {
    const response = await fetch(`${apiBase}/api/overlay${enabled === undefined ? '' : '/visibility'}`,
      enabled === undefined ? { cache: 'no-store' } : { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '오버레이 상태를 확인하지 못했습니다.');
    return data;
  }, [apiBase]);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (overlayMutation.current) return;
      const ticket = generation.current;
      try { const data = await requestOverlay(); if (active && ticket === generation.current && !overlayMutation.current) setOverlay(data); }
      catch (error) { if (active && ticket === generation.current) setMessage(error.message); }
    };
    refresh(); const timer = setInterval(refresh, 2500);
    window.addEventListener('focus', refresh);
    return () => { active = false; clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [requestOverlay]);
  const refreshDevices = useCallback(({ ask = false } = {}) => {
    if (permissionFlight.current) return permissionFlight.current;
    const run = async () => {
      if (ask) { setDeviceBusy(true); setMessage('브라우저의 마이크 접근 허용을 확인해 주세요.'); }
      try {
        const result = await listAudioDevices(navigator.mediaDevices, navigator.permissions, { ask });
        if (!mounted.current) return;
        if (!['denied', 'unavailable'].includes(result.permission)) setDevices(result.devices);
        if (ask) setMessage(result.permission === 'denied' ? '마이크 접근이 차단되었습니다. 주소 표시줄의 사이트 권한에서 마이크를 허용해 주세요.'
          : result.permission === 'unavailable' ? '장치 연결과 Windows·브라우저 마이크 권한을 확인해 주세요.'
          : '장치 목록을 갱신했습니다. 확인용 마이크는 바로 종료했으며 녹음하지 않았습니다.');
        await refreshOutputs?.();
      } catch { if (mounted.current) setMessage('장치 목록을 읽지 못했습니다. 연결 상태를 확인해 주세요.'); }
      finally { if (mounted.current && ask) setDeviceBusy(false); }
    };
    const flight = run();
    if (ask) { permissionFlight.current = flight; flight.finally(() => { if (permissionFlight.current === flight) permissionFlight.current = null; }); }
    return flight;
  }, [refreshOutputs]);
  useEffect(() => {
    mounted.current = true; refreshDevices();
    const changed = () => refreshDevices();
    navigator.mediaDevices?.addEventListener?.('devicechange', changed);
    return () => { mounted.current = false; navigator.mediaDevices?.removeEventListener?.('devicechange', changed); };
  }, [refreshDevices]);
  const controls = controlsFromHost(host, overlay), speech = voiceCapability(host.status);
  const change = async (key, value) => {
    const audio = host.audioOutput;
    if (key === 'overlay') {
      if (overlayMutation.current) return;
      overlayMutation.current = true; generation.current += 1; setOverlayBusy(true); setMessage('');
      try { const data = await requestOverlay(value); if (mounted.current) setOverlay(data); }
      catch (error) { if (mounted.current) setMessage(error.message); }
      finally { overlayMutation.current = false; if (mounted.current) setOverlayBusy(false); }
    } else if (key === 'spotter' && value !== host.spotter.enabled) await host.spotter.toggle();
    else if (key === 'pitwall' && value !== host.wallEnabled) host.toggleWall();
    else if (key === 'microphone') host.changeInputDevice(value);
    else if (key === 'output') await audio.changeOutput(value === 'default' ? '' : value);
    else if (key === 'armed' && value !== host.armed && (!value || speech.available)) await host.prepare();
    else if (key === 'mode' && host.phase === 'idle') host.setMode(value);
    else if (key === 'speak') host.setSpeak(value);
    else if (key === 'volume') audio.changeVolume(value / 100);
    else if (key === 'muted' && value !== audio.muted) audio.toggleMute();
    else if (key === 'rate') audio.changeRate(value);
    else if (key === 'spotterRate') audio.changeSpotterRate(value);
    else if (key === 'pitwallRate') audio.changePitwallRate(value);
    else if (key === 'effect') audio.changeEffect(value);
    else if (key === 'spotterSettings') await host.spotter.changeSettings(value);
    else if (key === 'wallPreferences') host.changeWallPreferences(value);
  };
  const runtime = { controls, change, devices, message: message || host.error || host.spotter.error || host.audioOutput.message,
    refreshDevices, deviceBusy, speech, host, apiBase, overlay, overlayBusy };
  return <RuntimeContext.Provider value={runtime}>{typeof children === 'function' ? children(runtime) : children}</RuntimeContext.Provider>;
}

export function DesktopRuntimeProvider({ apiBase, visible, onSaved, children }) {
  const [workspace, setWorkspace] = useState(() => workspaceFromHash(window.location.hash));
  useEffect(() => {
    const changed = () => setWorkspace(workspaceFromHash(window.location.hash));
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  return <LiveCoach apiBase={apiBase} visible={visible ?? ['live', 'history'].includes(workspace)} monitor onSaved={onSaved}>
    {host => <RuntimeBridge host={host}>{children}</RuntimeBridge>}
  </LiveCoach>;
}
