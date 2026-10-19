// Ask only from a user action, then release every track before reading the device list.
export async function listAudioDevices(mediaDevices, permissions, { ask = false } = {}) {
  if (!mediaDevices?.enumerateDevices) return { devices: [], permission: 'unavailable' };
  let permission = 'unknown';
  if (ask) {
    let stream;
    try {
      try { permission = (await permissions?.query?.({ name: 'microphone' }))?.state || 'unknown'; }
      catch { /* Some browsers do not expose microphone permission queries. */ }
      if (permission === 'denied') return { devices: [], permission: 'denied' };
      if (!mediaDevices.getUserMedia) return { devices: [], permission: 'unavailable' };
      stream = await mediaDevices.getUserMedia({ audio: true });
      permission = 'granted';
    } catch (error) {
      return { devices: [], permission: ['NotAllowedError', 'SecurityError'].includes(error?.name) ? 'denied' : 'unavailable' };
    } finally {
      for (const track of stream?.getTracks?.() || []) {
        try { track.stop(); } catch { /* Still release the remaining tracks. */ }
      }
    }
  }
  const devices = await mediaDevices.enumerateDevices();
  return { permission, devices: devices.filter(d => ['audioinput', 'audiooutput'].includes(d.kind) && d.deviceId && d.deviceId !== 'default') };
}
