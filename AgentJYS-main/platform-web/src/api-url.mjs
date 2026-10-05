export function apiUrl(path) {
  const base = globalThis.document?.querySelector('meta[name="aether-base"]')?.content || '';
  return base + path;
}
