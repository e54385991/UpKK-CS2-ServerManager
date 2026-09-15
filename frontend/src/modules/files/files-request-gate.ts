export class RequestGate {
  private current = 0;

  next(): number {
    this.current += 1;
    return this.current;
  }

  invalidate(): void {
    this.current += 1;
  }

  isCurrent(requestId: number): boolean {
    return requestId === this.current;
  }
}

export function applyIfCurrent<T>(
  gate: RequestGate,
  requestId: number,
  apply: () => T,
): T | undefined {
  if (!gate.isCurrent(requestId)) return undefined;
  return apply();
}
