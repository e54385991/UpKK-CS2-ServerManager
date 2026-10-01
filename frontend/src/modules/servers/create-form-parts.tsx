"use client";

import { Label } from "@/shared/ui/input";
import {
type ReactNode
} from "react";

export type Captcha = { token: string; imageUrl: string; enabled: boolean };

export const GAME_MODES = [
  "competitive",
  "casual",
  "wingman",
  "deathmatch",
  "armsrace",
  "demolition",
  "custom",
] as const;

export function SummaryItem({
  label,
  value,
  className,
  testId,
}: {
  label: string;
  value: string;
  className?: string;
  testId?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-xs text-fg-subtle">{label}</dt>
      <dd
        className="mt-0.5 font-mono text-sm text-fg break-all"
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  children,
  className,
  hint,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  className?: string;
  hint?: string;
}) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint ? <p className="mt-1 text-xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}

