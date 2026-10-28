// Adapt the single production radio host to the approved desktop controls.
export function controlsFromHost(host, overlay) {
  const audio = host.audioOutput;
  return { overlay: overlay?.auto_show === true, spotter: host.spotter.enabled, pitwall: host.wallEnabled,
    microphone: host.inputDevice || 'default', output: audio.outputId || 'default', armed: host.armed,
    mode: host.mode, speak: host.speak, volume: Math.round(audio.volume * 100), muted: audio.muted,
    rate: audio.rate, spotterRate: audio.spotterRate, pitwallRate: audio.pitwallRate,
    effect: audio.effect, spotterSettings: host.spotter.settings, wallPreferences: host.wallPreferences };
}

export function workspaceFromHash(hash) {
  const value = hash.replace(/^#/, '');
  if (!value) return 'driver';
  if (value.startsWith('guide-')) return 'guide';
  return ['driver', 'summary', 'corners', 'laps', 'expert', 'battle', 'setup', 'live', 'overlay', 'history', 'settings', 'guide'].includes(value) ? value : 'summary';
}
