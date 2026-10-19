export const unavailableVoice = { available: false, reason: 'unavailable', verified: false };

export function voiceCapability(status) {
  const speech = status?.speech;
  if (!speech?.enabled || !speech?.recognition_installed || !speech?.credential_configured) return unavailableVoice;
  return { available: true, reason: 'configured', verified: speech.connection_verified === true };
}

export async function loadVoiceCapability(fetcher = fetch, signal, apiBase = '') {
  try {
    const response = await fetcher(`${apiBase}/api/live-coach/status`, { cache: 'no-store', signal });
    return response.ok ? voiceCapability(await response.json()) : unavailableVoice;
  } catch { return unavailableVoice; }
}
