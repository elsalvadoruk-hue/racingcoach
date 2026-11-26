import { useEffect, useRef, useState } from 'react';
import { deviceIdentity, sameDevice } from './voiceDeviceIdentity';

const key = game => `iracing.voice.resume.v1:${game}`;
const read = game => { try { return JSON.parse(localStorage.getItem(key(game))) || {}; } catch { return {}; } };
const write = (game, value) => { try { localStorage.setItem(key(game), JSON.stringify(value)); } catch { /* Optional preference. */ } };

export default function useVoiceAutoResume({ game, input, audio, spotter, localRadio, wallEnabled, setWallEnabled }) {
  const [message, setMessage] = useState('');
  const outputId=(audio.applied || audio).outputId;
  const logsLoaded=!!localRadio.logAuto?.state;
  const current = useRef();
  current.current = { input, audio, spotter, localRadio, wallEnabled, setWallEnabled };
  const restoreWall = useRef(null);
  const attempted = useRef({});
  const previous = useRef({ game });
  // Remember only successful starts. Initial false values never erase the last session.
  useEffect(() => {
    let closed = false;
    const before = previous.current;
    previous.current = { game, local:localRadio.enabled, spotter:spotter.enabled, wall:wallEnabled };
    const settings = read(game);
    if (before.game === game && !audio.standby) {
      if (before.local && !localRadio.enabled) settings.local = false;
      if (before.spotter && !spotter.enabled) settings.spotter = false;
      if (before.wall && !wallEnabled) settings.wall = false;
      write(game,settings);
    }
    if (audio.standby) return undefined;
    deviceIdentity(input, outputId).then(devices => {
      if (closed || !devices) return;
      const saved = read(game);
      if (localRadio.enabled && devices.mic) { saved.local = true; saved.mic = devices.mic; }
      if (sameDevice(audio.playbackConfirmed, devices.speaker) && devices.mic && devices.speaker && (spotter.enabled || wallEnabled)) {
        saved.radioMic = devices.mic; saved.speaker = devices.speaker;
        saved.spotter = spotter.enabled; saved.wall = wallEnabled;
      }
      write(game,saved);
    }).catch(() => {});
    return () => { closed = true; };
  }, [game, input, audio.standby, audio.playbackConfirmed, outputId, localRadio.enabled, spotter.enabled, wallEnabled]);
  useEffect(() => {
    if (restoreWall.current !== null && spotter.enabled) {
      setWallEnabled(restoreWall.current); restoreWall.current = null;
    }
  }, [spotter.enabled, setWallEnabled]);
  useEffect(() => {
    if (audio.standby || !logsLoaded) return undefined;
    let closed = false;
    const restore = async () => {
      const state = current.current;
      const saved = read(game);
      const devices = await deviceIdentity(state.input, (state.audio.applied || state.audio).outputId);
      if (closed || !devices) return;
      const micOK = sameDevice(saved.mic, devices.mic);
      if (state.localRadio.enabled && saved.mic && !micOK) {
        state.localRadio.stop(); setMessage('마이크가 바뀌어 음성 인식을 중지했습니다. 장치를 확인하고 다시 켜 주세요.');
      }
      if (attempted.current[game]) return;
      attempted.current[game] = true;
      if (saved.local && micOK && !state.localRadio.enabled && state.localRadio.logAuto.state.enabled) {
        await state.localRadio.prepare();
      } else if (saved.local && !micOK) setMessage('이전 마이크를 확인하지 못해 음성 인식 자동 시작을 보류했습니다.');
      if (closed) return;
      if ((saved.spotter || saved.wall) && sameDevice(saved.radioMic,devices.mic) && sameDevice(saved.speaker,devices.speaker)) {
        if (saved.spotter && !state.spotter.enabled && !state.spotter.preparing) {
          restoreWall.current = !!saved.wall;
          await state.spotter.toggle();
        } else state.setWallEnabled(!!saved.wall);
      } else if (saved.spotter || saved.wall) setMessage('마이크·출력장치가 달라 자동 무전 시작을 보류했습니다. 테스트 소리와 장치를 확인하세요.');
    };
    restore().catch(() => { if (!closed) setMessage('자동 시작을 완료하지 못했습니다. 음성 코치에서 장치를 확인하세요.'); });
    navigator.mediaDevices?.addEventListener?.('devicechange', restore);
    return () => { closed = true; navigator.mediaDevices?.removeEventListener?.('devicechange', restore); };
  }, [game, audio.standby, logsLoaded, input]);
  return message;
}
