// The first file is the pinned actual lap; the rest only corroborate it.
export function selectPrimary(files, primary) {
  return files.includes(primary) ? [primary, ...files.filter(name => name !== primary)] : files;
}

export function retainPrimary(previous, next) {
  return selectPrimary(next, previous[0]);
}
