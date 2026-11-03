import { voiceSelections } from './voiceLibrary';

const DEFAULT_VOICES = { coach: 'Algenib', race: 'Charon', strategy: 'Orus' };
const ROLES = { spotter: ['spotter'], pitwall: ['coach', 'race', 'strategy'] };
export const voiceScope = role => role === 'spotter' ? 'spotter' : 'pitwall';
export const shortPack = pack => pack?.radioStyle?.length === 'short';
const packs = library => library?.view?.packs || [];
const selectedPack = (role, settings, library) => packs(library).find(p => p.key === voiceSelections(settings)[role]);
export function voiceLength(scope, settings, library) {
  return ROLES[scope].some(role => shortPack(selectedPack(role, settings, library))) ? 'short' : 'default';
}
export function baseVoice(role, settings, library) {
  const current = selectedPack(role, settings, library);
  return shortPack(current) ? current.radioStyle.voice : role === 'spotter' ? settings?.voice || 'Fenrir' : DEFAULT_VOICES[role];
}
export function matchingShortPack(role, voice, library) {
  return packs(library).filter(p => shortPack(p) && p.canSelect !== false && p.roles?.[role] > 0 && p.radioStyle.voice === voice)
    .sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }))[0];
}
export function canShorten(scope, settings, library) {
  const choices = voiceSelections(settings);
  return ROLES[scope].every(role => {
    const current = selectedPack(role, settings, library);
    if (choices[role] && !current) return false;
    return (!current || shortPack(current)) && Boolean(matchingShortPack(role, baseVoice(role, settings, library), library));
  });
}
const patch = (choices, extra = {}) => ({ voice_packs: choices, custom_pack: choices.spotter || null, ...extra });
export function lengthPatch(scope, length, settings, library) {
  if (!ROLES[scope] || !['short', 'default'].includes(length)) return null;
  if (length === 'short' && !canShorten(scope, settings, library)) return null;
  const choices = { ...voiceSelections(settings) };
  for (const role of ROLES[scope]) {
    if (length === 'short') choices[role] = matchingShortPack(role, baseVoice(role, settings, library), library).key;
    else if (shortPack(selectedPack(role, settings, library))) delete choices[role];
  }
  return patch(choices, scope === 'spotter' ? { voice: baseVoice('spotter', settings, library) } : {});
}
export function voicePatch(role, value, settings, library) {
  const choices = { ...voiceSelections(settings) }, scope = voiceScope(role);
  if (value.startsWith('pack:')) {
    // An unrelated custom voice keeps its own wording; clear this group's short variants.
    for (const item of ROLES[scope]) if (shortPack(selectedPack(item, settings, library))) delete choices[item];
    choices[role] = value.slice(5);
    return patch(choices);
  }
  const voice = role === 'spotter' ? value : DEFAULT_VOICES[role];
  if (voiceLength(scope, settings, library) === 'short') {
    const target = matchingShortPack(role, voice, library);
    if (!target) return null;
    choices[role] = target.key;
  } else delete choices[role];
  return patch(choices, role === 'spotter' ? { voice } : {});
}
