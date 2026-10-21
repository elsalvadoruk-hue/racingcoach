// Keep the same own/reference colors in charts, maps, legends and readouts.
const PALETTES = {
  iracing: { target: '#66c6ef', reference: '#f0ad72', canvas: '#151e2a', grid: '#2a3a4c', muted: '#92a3b8', cursor: '#b8c7d8' },
  lmu: { target: '#FF5A73', reference: '#7FB2FF', canvas: '#0B1022', grid: '#26304F', muted: '#9AA3BF', cursor: '#C9D0E6' },
};
export const comparisonColors = () => PALETTES[document.documentElement.dataset.game === 'lmu' ? 'lmu' : 'iracing'];
// Compatibility for older callers. New charts read comparisonColors() at render time.
export const COMPARISON_COLORS = PALETTES.iracing;

// Canvas cannot resolve var() itself. Read the CSS decoration tokens when drawing.
export const chartDecoration = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
