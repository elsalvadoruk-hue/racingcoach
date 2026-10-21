// Keep the same own/reference colors in charts, maps, legends and readouts.
const PALETTES = {
  iracing: { target: '#66c6ef', reference: '#f0ad72', canvas: '#151e2a', grid: '#2a3a4c', muted: '#92a3b8', cursor: '#b8c7d8' },
  lmu: { target: '#8AB4DE', reference: '#D7B088', canvas: '#182436', grid: '#2B3C53', muted: '#A6B2C4', cursor: '#D0DAE7' },
};
export const comparisonColors = () => PALETTES[document.documentElement.dataset.game === 'lmu' ? 'lmu' : 'iracing'];
// Compatibility for older callers. New charts read comparisonColors() at render time.
export const COMPARISON_COLORS = PALETTES.iracing;

// Canvas cannot resolve var() itself. Read the CSS decoration tokens when drawing.
export const chartDecoration = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
