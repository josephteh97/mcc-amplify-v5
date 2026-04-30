// Shared frontend helpers.

export function basename(path) {
  return (path || '').split('/').pop() || '';
}
