import { comparisonColors, COMPARISON_COLORS } from './chartTheme';

afterEach(() => { delete document.documentElement.dataset.game; });

test('the default and explicit iRacing palettes keep the original values', () => {
  delete document.documentElement.dataset.game;
  const original = { target: '#66c6ef', reference: '#f0ad72', canvas: '#151e2a', grid: '#2a3a4c', muted: '#92a3b8', cursor: '#b8c7d8' };
  expect(comparisonColors()).toEqual(original);
  document.documentElement.dataset.game = 'iracing';
  expect(comparisonColors()).toEqual(original);
  expect(COMPARISON_COLORS).toEqual(original);
});

test('LMU is read on each call and does not change the legacy palette', () => {
  document.documentElement.dataset.game = 'lmu';
  expect(comparisonColors()).toEqual({ target: '#8AB4DE', reference: '#D7B088', canvas: '#182436', grid: '#2B3C53', muted: '#A6B2C4', cursor: '#D0DAE7' });
  expect(COMPARISON_COLORS.target).toBe('#66c6ef');
  document.documentElement.dataset.game = 'iracing';
  expect(comparisonColors()).toBe(COMPARISON_COLORS);
});
