import { gameKey, readGameItem, writeGameItem, removeGameItem } from './gameStorage';

beforeEach(() => localStorage.clear());
afterEach(() => jest.restoreAllMocks());

test('each game starts from legacy values and game values take precedence including empty strings', () => {
  localStorage.setItem('setting', 'legacy');
  expect(readGameItem('setting', 'iracing')).toBe('legacy');
  expect(readGameItem('setting', 'lmu')).toBe('legacy');
  writeGameItem('setting', 'lmu', '');
  expect(readGameItem('setting', 'lmu')).toBe('');
  expect(readGameItem('setting', 'iracing')).toBe('legacy');
  expect(localStorage.getItem('setting')).toBe('legacy');
  expect(gameKey('setting', 'unknown')).toBe('setting.iracing');
  removeGameItem('setting', 'lmu');
  expect(readGameItem('setting', 'lmu')).toBe('legacy');
  expect(localStorage.getItem('setting')).toBe('legacy');
});

test('blocked storage is harmless', () => {
  for (const method of ['getItem', 'setItem', 'removeItem']) jest.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error('blocked'); });
  expect(readGameItem('setting', 'lmu')).toBeNull();
  expect(() => writeGameItem('setting', 'lmu', 'value')).not.toThrow();
  expect(() => removeGameItem('setting', 'lmu')).not.toThrow();
});
