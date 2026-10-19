import React, { useEffect, useMemo, useRef, useState } from 'react';
import { imageryTiles } from '../lib/geographicMap';

// Tiles are asked for a little beyond the visible map, so a moving camera (dragging, the ghost replay's
// "따라가기") finds the next tiles already there.
const MARGIN = .2;

export default function GeographicMapTiles({ view, viewport, origin, onStatus }) {
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
  const requested = useMemo(() => imageryTiles(
    { x: view.x - view.width * MARGIN, y: view.y - view.height * MARGIN, width: view.width * (1 + 2 * MARGIN), height: view.height * (1 + 2 * MARGIN) },
    { width: viewport.width * (1 + 2 * MARGIN), height: viewport.height * (1 + 2 * MARGIN) }, origin), [view, viewport, origin]);
  const requestedKey = `${origin?.x},${origin?.y}:` + requested.map(tile => tile.key).join('|');
  const requestedRef = useRef(requested);
  requestedRef.current = requested;
  const [tiles, setTiles] = useState(requested);
  const [previous, setPrevious] = useState([]);       // the last grid, kept underneath until the new one has loaded
  const [settled, setSettled] = useState({});
  const tilesRef = useRef(tiles);
  tilesRef.current = tiles;
  const activeKey = `${origin?.x},${origin?.y}:` + tiles.map(tile => tile.key).join('|');
  const activeKeyRef = useRef(activeKey);
  activeKeyRef.current = activeKey;
  useEffect(() => {
    // Panning can generate many pointer events; request a grid once it has stayed the same for a moment.
    // Only a change of the tile grid counts: a camera that moves every frame inside the same tiles must not
    // keep putting the request off.
    if (requestedKey === activeKeyRef.current) return undefined;
    const timer = setTimeout(() => {
      setPrevious(tilesRef.current);
      setTiles(requestedRef.current);
    }, 120);
    return () => clearTimeout(timer);
  }, [requestedKey]);
  useEffect(() => {
    const values = tiles.map(tile => settled[tile.key]);
    const loaded = values.filter(value => value === 'loaded').length;
    const failed = values.filter(value => value === 'failed').length;
    const status = loaded + failed < tiles.length ? 'loading' : loaded === 0 ? 'unavailable' : failed ? 'partial' : 'ready';
    onStatus(status);
    if (status !== 'loading') setPrevious(current => (current.length ? [] : current));
  }, [tiles, settled, onStatus]);
  const finish = (tile, value, key) => {
    if (activeKeyRef.current !== key) return;
    setSettled(previous => previous[tile.key] === value ? previous : { ...previous, [tile.key]: value });
  };
  const current = new Set(tiles.map(tile => tile.key));
  return <g ref={groupRef} data-testid="map-geographic-background" pointerEvents="none">
    {visible && previous.filter(tile => !current.has(tile.key) && settled[tile.key] === 'loaded').map(tile => <image key={`old-${tile.key}`} href={tile.url} x={tile.x} y={tile.y} width={tile.width} height={tile.height} preserveAspectRatio="none" opacity=".78" />)}
    {visible && tiles.map(tile => <image key={tile.key} href={tile.url} x={tile.x} y={tile.y} width={tile.width} height={tile.height} preserveAspectRatio="none" opacity=".78"
      onLoad={() => finish(tile, 'loaded', activeKey)} onError={() => finish(tile, 'failed', activeKey)} />)}
  </g>;
}
