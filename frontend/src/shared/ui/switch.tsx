import { cn } from "@/shared/lib/cn";

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  id,
  label,
  description,
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  id: string;
  label: string;
  description?: string;
}) {
  const accessibleLabel = description ? `${label}: ${description}` : label;
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={accessibleLabel}
      title={description}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:opacity-50",
        checked
          ? "border-primary/40 bg-primary"
          : "border-line-strong bg-surface-overlay",
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-primary-foreground shadow-sm transition-transform",
          checked ? "translate-x-6" : "translate-x-1",
        )}
      />
    </button>
  );
}
