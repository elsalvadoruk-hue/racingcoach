const RADIUS = 6378137;
export const WORLD_HALF = Math.PI * RADIUS;
export const IMAGERY_SOURCE = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer';
export const IMAGERY_CREDIT = 'Esri, Vantor, Earthstar Geographics, and the GIS User Community';

export function gpsToMercator({ lat, lon }) {
  const radians = Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180;
  return { x: lon * Math.PI / 180 * RADIUS, y: -RADIUS * Math.log(Math.tan(Math.PI / 4 + radians / 2)) };
}

// view is in Mercator metres relative to origin (see projectComparisonPaths); tiles come back in the same frame.
export function imageryTiles(local, viewport, origin = { x: 0, y: 0 }) {
  const view = { ...local, x: local.x + origin.x, y: local.y + origin.y };
  const resolution = Math.max(view.width / Math.max(1, viewport.width), view.height / Math.max(1, viewport.height));
  let zoom = Math.max(3, Math.min(19, Math.floor(Math.log2(2 * WORLD_HALF / (256 * resolution)))));
  let bounds;
  const extent = level => {
    const count = 2 ** level, size = 2 * WORLD_HALF / count;
    return { count, size,
      left: Math.max(0, Math.floor((view.x + WORLD_HALF) / size)),
      right: Math.min(count - 1, Math.floor((view.x + view.width + WORLD_HALF) / size)),
      top: Math.max(0, Math.floor((view.y + WORLD_HALF) / size)),
      bottom: Math.min(count - 1, Math.floor((view.y + view.height + WORLD_HALF) / size)),
    };
  };
  // Bound network and DOM work even in unusual, wide viewports.
  for (;;) {
    bounds = extent(zoom);
    if ((bounds.right - bounds.left + 1) * (bounds.bottom - bounds.top + 1) <= 40 || zoom === 3) break;
    zoom -= 1;
  }
  const tiles = [];
  for (let row = bounds.top; row <= bounds.bottom; row += 1) {
    for (let col = bounds.left; col <= bounds.right; col += 1) {
      tiles.push({ key: `${zoom}/${row}/${col}`, url: `${IMAGERY_SOURCE}/tile/${zoom}/${row}/${col}`,
        x: -WORLD_HALF + col * bounds.size - origin.x, y: -WORLD_HALF + row * bounds.size - origin.y,
        width: bounds.size, height: bounds.size });
    }
  }
  return tiles;
}
