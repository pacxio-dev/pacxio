// Match Python identity.py without relying on JSON float formatting.
export function canonical(value) {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return String(value);
  if (typeof value === 'number') {
    const bytes = new ArrayBuffer(8); new DataView(bytes).setFloat64(0, value || 0);
    return 'n' + [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, '0')).join('');
  }
  if (typeof value === 'string') return 's' + JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
}
