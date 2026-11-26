// Browser IDs stay local. Resolve default aliases to the actual device so a
// changed Windows default cannot silently restore a different microphone.
export function resolveDevice(devices, kind, selected = '') {
  const rows = devices.filter(d => d.kind === kind);
  const wanted = selected || 'default';
  const item = rows.find(d => d.deviceId === wanted);
  if (!item?.label) return null;
  const real = ['default', 'communications'].includes(wanted)
    ? rows.find(d => !['default', 'communications'].includes(d.deviceId) && d.groupId && d.groupId === item.groupId)
    : item;
  return real?.deviceId && real.label ? { id: real.deviceId, group: real.groupId || '', label: real.label } : null;
}
export async function deviceIdentity(input, output) {
  if (!navigator.mediaDevices?.enumerateDevices) return null;
  const devices = await navigator.mediaDevices.enumerateDevices();
  return { mic: resolveDevice(devices, 'audioinput', input), speaker: resolveDevice(devices, 'audiooutput', output) };
}
export const sameDevice = (a, b) => !!a && !!b && a.id === b.id && a.group === b.group && a.label === b.label;
