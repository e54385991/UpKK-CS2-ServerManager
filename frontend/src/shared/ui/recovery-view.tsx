import Link from "next/link";
import { Button } from "./button";

export function RecoveryView({
  title,
  description,
  overviewLabel,
  retryLabel,
  onRetry,
  code,
}: {
  title: string;
  description: string;
  overviewLabel: string;
  retryLabel?: string;
  onRetry?: () => void;
  code?: string;
}) {
  return (
    <main className="flex min-h-[70dvh] flex-1 items-center justify-center px-6 py-12">
      <div className="w-full max-w-md space-y-5 text-center">
        {code && <p className="font-mono text-sm text-fg-muted">{code}</p>}
        <h1 className="text-2xl font-semibold text-fg">{title}</h1>
        <p className="text-sm leading-relaxed text-fg-muted">{description}</p>
        <div className="flex flex-wrap justify-center gap-3">
          {onRetry && <Button type="button" onClick={onRetry}>{retryLabel}</Button>}
          <Button variant="outline" asChild>
            <Link href="/overview">{overviewLabel}</Link>
          </Button>
        </div>
      </div>
    </main>
  );
}
