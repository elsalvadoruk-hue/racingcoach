import { baseVoice, canShorten, lengthPatch, voiceLength, voicePatch } from './voiceLength';

const make = (role, voice, version = '1.1.0') => ({ key: `brief-${role}-${voice.toLowerCase()}@${version}`, version,
  roles: { [role]: 1 }, radioStyle: { length: 'short', voice } });
const rows = [make('spotter', 'Fenrir'), make('spotter', 'Aoede'), make('coach', 'Algenib'), make('race', 'Charon'), make('strategy', 'Orus')];
const library = { view: { packs: rows } };
test('spotter and pit wall length selections are independent and reuse the existing saved settings contract', () => {
  const short = { voice: 'Fenrir', ...lengthPatch('spotter', 'short', { voice: 'Fenrir' }, library) };
  expect(short.voice_packs).toEqual({ spotter: rows[0].key });
  const both = { ...short, ...lengthPatch('pitwall', 'short', short, library) };
  expect(Object.keys(both.voice_packs)).toHaveLength(4);
  const normalSpotter = { ...both, ...lengthPatch('spotter', 'default', both, library) };
  expect(voiceLength('spotter', normalSpotter, library)).toBe('default');
  expect(voiceLength('pitwall', normalSpotter, library)).toBe('short');
  expect(normalSpotter.voice).toBe('Fenrir');
});
test('changing a short spotter voice selects the matching wording pack, and normal restores that voice', () => {
  const original = { voice: 'Fenrir', ...lengthPatch('spotter', 'short', {}, library) };
  const changed = { ...original, ...voicePatch('spotter', 'Aoede', original, library) };
  expect(changed.voice).toBe('Aoede');
  expect(changed.custom_pack).toBe(rows[1].key);
  const normal = { ...changed, ...lengthPatch('spotter', 'default', changed, library) };
  expect(normal.voice).toBe('Aoede');
  expect(normal.custom_pack).toBeNull();
  expect(baseVoice('spotter', { voice: 'Fenrir', voice_packs: { spotter: rows[1].key } }, library)).toBe('Aoede');
});
test('unavailable voices cannot silently change to another speaker or synthesize new audio', () => {
  expect(canShorten('spotter', { voice: 'Schedar' }, library)).toBe(false);
  expect(lengthPatch('spotter', 'short', { voice: 'Schedar' }, library)).toBeNull();
  const settings = { voice_packs: { spotter: rows[0].key } };
  expect(voicePatch('spotter', 'Schedar', settings, library)).toBeNull();
  expect(lengthPatch('pitwall', 'short', {}, { view: { packs: rows.slice(0, 4) } })).toBeNull();
  expect(canShorten('spotter', { voice_packs: { spotter: 'missing@1' } }, library)).toBe(false);
});
test('unrelated custom voices are preserved, and selecting one leaves this group in normal mode', () => {
  const view = { view: { packs: [...rows, { key: 'custom@1', roles: { coach: 1 } }] } };
  expect(canShorten('pitwall', { voice_packs: { coach: 'custom@1' } }, view)).toBe(false);
  const both = { ...lengthPatch('spotter', 'short', {}, view) };
  const all = { ...both, ...lengthPatch('pitwall', 'short', both, view) };
  const changed = { ...all, ...voicePatch('coach', 'pack:custom@1', all, view) };
  expect(changed.voice_packs).toEqual({ spotter: rows[0].key, coach: 'custom@1' });
  expect(voiceLength('pitwall', changed, view)).toBe('default');
});
test('the newer compact pack supersedes the full pack without multiplying voice options', () => {
  const view = { view: { packs: [make('spotter', 'Fenrir', '1.0.0'), ...rows] } };
  expect(lengthPatch('spotter', 'short', {}, view).custom_pack).toBe(rows[0].key);
});
