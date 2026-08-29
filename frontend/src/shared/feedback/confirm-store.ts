export type ConfirmTone = "danger" | "default";

export type ConfirmOptions = {
  title?: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
};

export type ConfirmRequest = {
  readonly id: number;
  readonly options: ConfirmOptions;
};

type Listener = () => void;

let nextId = 1;
let current: ConfirmRequest | null = null;
const queued: Array<{
  options: ConfirmOptions;
  resolve: (value: boolean) => void;
}> = [];
const resolvers = new Map<number, (value: boolean) => void>();
const listeners = new Set<Listener>();

function emit() {
  for (const listener of listeners) listener();
}

function promote() {
  if (current) return;
  const next = queued.shift();
  if (!next) {
    emit();
    return;
  }
  const request: ConfirmRequest = { id: nextId++, options: next.options };
  current = request;
  resolvers.set(request.id, next.resolve);
  emit();
}

export function subscribeConfirm(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getConfirmSnapshot() {
  return current;
}

export function getConfirmServerSnapshot() {
  return null;
}

export function resolveConfirm(value: boolean) {
  if (!current) return;
  const resolve = resolvers.get(current.id);
  resolvers.delete(current.id);
  current = null;
  resolve?.(value);
  promote();
}

export function confirm(input: string | ConfirmOptions): Promise<boolean> {
  const options: ConfirmOptions =
    typeof input === "string"
      ? { description: input, tone: "danger" }
      : { tone: "danger", ...input };

  return new Promise((resolve) => {
    queued.push({ options, resolve });
    promote();
  });
}
