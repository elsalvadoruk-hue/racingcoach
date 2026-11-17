// A bounded, silent decoder comparison. No output device or live effect context
// is opened, and the original payload is never retained or logged.
const LIMIT = 2 * 1024 * 1024;
const controlWav = () => {
  const bytes = new ArrayBuffer(140), view = new DataView(bytes);
  const word = (at, value) => [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  word(0, 'RIFF'); view.setUint32(4, 132, true); word(8, 'WAVE'); word(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 24000, true); view.setUint32(28, 48000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  word(36, 'data'); view.setUint32(40, 96, true);
  return bytes;
};

export async function probeAudioDecode(clip) {
  const Context = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!Context) return 'unavailable';
  const encoded = String(clip).replace(/^data:[^,]*,/, '');
  if (encoded.length > Math.ceil(LIMIT * 4 / 3)) return 'oversize';
  let timer;
  try {
    const context = new Context(1, 1, 24000);
    const decode = async buffer => {
      try { await context.decodeAudioData(buffer); return true; } catch { return false; }
    };
    let bytes;
    try { bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0)).buffer; } catch { bytes = new ArrayBuffer(0); }
    const comparison = Promise.all([decode(bytes), decode(controlWav())]).then(([source, control]) =>
      source && control ? 'decoded' : !source && control ? 'source_failed' : 'both_failed');
    return await Promise.race([comparison, new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 1000); })]);
  } catch { return 'unavailable'; }
  finally { clearTimeout(timer); }
}
