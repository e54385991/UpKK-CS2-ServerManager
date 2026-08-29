import { cn } from "@/shared/lib/cn";
import type { ComponentProps } from "react";

type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "primary";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-overlay text-fg-muted border-line",
  ok: "bg-ok-muted text-ok border-ok/30",
  warn: "bg-warn-muted text-warn border-warn/30",
  danger: "bg-danger-muted text-danger border-danger/30",
  info: "bg-info-muted text-info border-info/30",
  primary: "bg-primary-muted text-primary border-primary/30",
};

export function Badge({
  className,
  tone = "neutral",
  ...props
}: ComponentProps<"span"> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function StatusDot({
  tone = "neutral",
  pulse = false,
  className,
}: {
  tone?: Tone;
  pulse?: boolean;
  className?: string;
}) {
  const color: Record<Tone, string> = {
    neutral: "bg-fg-subtle",
    ok: "bg-ok",
    warn: "bg-warn",
    danger: "bg-danger",
    info: "bg-info",
    primary: "bg-primary",
  };
  return (
    <span className={cn("relative inline-flex size-2", className)}>
      {pulse ? (
        <span
          className={cn(
            "absolute inline-flex size-full animate-ping rounded-full opacity-60",
            color[tone],
          )}
        />
      ) : null}
      <span
        className={cn(
          "relative inline-flex size-2 rounded-full",
          color[tone],
        )}
      />
    </span>
  );
}
