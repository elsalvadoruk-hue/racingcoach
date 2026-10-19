import React, { useEffect, useMemo, useRef, useState } from 'react';
import { imageryTiles } from '../lib/geographicMap';

export default function GeographicMapTiles({ view, viewport, onStatus }) {
  const groupRef = useRef(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const svg = groupRef.current?.ownerSVGElement;
    if (!svg) return undefined;
    const observer = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { rootMargin: '256px' });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);
  const requested = useMemo(() => imageryTiles(view, viewport), [view, viewport]);
  const requestedKey = requested.map(tile => tile.key).join('|');
  const [tiles, setTiles] = useState(requested);
  const [settled, setSettled] = useState({});
  const activeKey = tiles.map(tile => tile.key).join('|');
  const activeKeyRef = useRef(activeKey);
  activeKeyRef.current = activeKey;
  useEffect(() => {
    // Panning can generate many pointer events; request only the settled grid.
    const timer = setTimeout(() => setTiles(requested), 120);
    return () => clearTimeout(timer);
  }, [requested, requestedKey]);
  useEffect(() => {
    const values = tiles.map(tile => settled[tile.key]);
    const loaded = values.filter(value => value === 'loaded').length;
    const failed = values.filter(value => value === 'failed').length;
    const status = loaded + failed < tiles.length ? 'loading' : loaded === 0 ? 'unavailable' : failed ? 'partial' : 'ready';
    onStatus(status);
  }, [tiles, settled, onStatus]);
  const finish = (tile, value, key) => {
    if (activeKeyRef.current !== key) return;
    setSettled(previous => previous[tile.key] === value ? previous : { ...previous, [tile.key]: value });
  };
  return <g ref={groupRef} data-testid="map-geographic-background" pointerEvents="none">
    {visible && tiles.map(tile => <image key={tile.key} href={tile.url} x={tile.x} y={tile.y} width={tile.width} height={tile.height} preserveAspectRatio="none" opacity=".78"
      onLoad={() => finish(tile, 'loaded', activeKey)} onError={() => finish(tile, 'failed', activeKey)} />)}
  </g>;
}
