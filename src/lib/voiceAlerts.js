import catalog from './voiceAlerts.json';

export function alertGroups(scope, game = 'iracing') {
  return catalog[scope].filter(g => !g.games || g.games.includes(game)).map(g => ({ ...g,
    items: g.items.filter(i => !i.games || i.games.includes(game)) }));
}

export function defaultAlerts(scope, game = 'iracing', legacy = {}) {
  const entries = alertGroups(scope, game).flatMap(g => g.items.map(item => [item.id,
    scope === 'pitwall' ? legacy.level !== 'off' && (legacy.level === 'all' || item.legacyImportant !== false) :
      item.id === 'pit_box' ? legacy.pit_box_enabled === true :
      ['class_approach', 'lapping', 'close', 'quali'].includes(item.id) ? legacy.traffic_enabled !== false :
      item.id === 'formation' ? legacy.formation_calls !== 0 : item.id === 'repeat' ? legacy.repeat_enabled !== false : true]));
  return Object.fromEntries(entries);
}

export function readAlerts(scope, game, saved = {}) {
  const defaults = defaultAlerts(scope, game, saved);
  return Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => [key,
    typeof saved.alerts?.[key] === 'boolean' ? saved.alerts[key] : fallback]));
}

const patterns = Object.entries(catalog).flatMap(([scope, groups]) => groups.flatMap(g => g.items.flatMap(item =>
  item.keys.map(key => [new RegExp(`^${key.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`), `${scope}.${item.id}`]))));
export function alertCategory(key = '') {
  return patterns.find(([pattern]) => pattern.test(key))?.[1];
}
export function categoryAllowed(name, spotter = {}, pitwall = {}) {
  if (!name) return true;
  const [scope, key] = name.split('.');
  if (scope === 'spotter' && ['repeat', 'clear'].includes(key) && spotter.overlap === false) return false;
  return (scope === 'spotter' ? spotter : pitwall)[key] !== false;
}
export function voiceEventAllowed(event, spotter, pitwall) {
  return (event.alert_categories || [alertCategory(event.key)]).every(name => categoryAllowed(name, spotter, pitwall));
}

// Old fields remain on the wire for compatibility with an already running backend.
export function spotterAlertPatch(settings, alerts) {
  return { alerts, pit_box_enabled: alerts.pit_box === true,
    traffic_enabled: ['class_approach', 'lapping', 'close', 'quali'].some(key => alerts[key] !== false),
    ...(alerts.formation && settings.formation_calls === 0 ? { formation_calls: 5 } : {}) };
}
