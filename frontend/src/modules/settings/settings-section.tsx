import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export function SettingsSection({
  id,
  title,
  description,
  children,
  className,
  testId,
}: {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      data-testid={testId}
      className={cn("scroll-mt-24 space-y-4", className)}
    >
      <div className="px-1">
        <h2 id={`${id}-title`} className="text-base font-semibold tracking-tight text-fg">
          {title}
        </h2>
        {description ? (
          <p className="mt-1 text-sm text-fg-muted">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}
