export function availablePostUpdateCommands<T extends { id: number }>(
  saved: readonly T[],
  selectedIds: readonly number[],
): T[] {
  const selected = new Set(selectedIds);
  return saved.filter((item) => !selected.has(item.id));
}

export function addPostUpdateCommand(
  ids: readonly number[],
  commandId: number,
): number[] {
  if (!commandId || ids.includes(commandId)) return [...ids];
  return [...ids, commandId];
}

export function removePostUpdateCommand(
  ids: readonly number[],
  index: number,
): number[] {
  return ids.filter((_, current) => current !== index);
}

export function movePostUpdateCommand(
  ids: readonly number[],
  index: number,
  delta: -1 | 1,
): number[] {
  const next = [...ids];
  const target = index + delta;
  if (index < 0 || target < 0 || index >= next.length || target >= next.length) {
    return next;
  }
  const item = next[index];
  if (item === undefined) return next;
  next.splice(index, 1);
  next.splice(target, 0, item);
  return next;
}
