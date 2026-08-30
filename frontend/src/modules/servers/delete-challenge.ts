export function randomDeleteCode(): string {
  return String(1000 + Math.floor(Math.random() * 9000));
}

export function deleteCodeMatches(typed: string, code: string): boolean {
  return typed.trim() === code;
}
