/**
 * Session / correlation ids for the browser. `crypto.randomUUID` is missing on
 * non-HTTPS LAN origins (typical 1Panel), so fall back to `getRandomValues`.
 */
export function randomId(): string {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  if (typeof webCrypto?.getRandomValues === "function") {
    return uuidFromBytes(webCrypto.getRandomValues(new Uint8Array(16)));
  }
  return `id-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

export function uuidFromBytes(bytes: Uint8Array): string {
  const next = bytes.length >= 16 ? bytes : Uint8Array.from({ length: 16 }, (_, i) => bytes[i] ?? 0);
  next[6] = ((next[6] ?? 0) & 0x0f) | 0x40;
  next[8] = ((next[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(next, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}
