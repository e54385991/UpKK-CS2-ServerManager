import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Crosshair } from "lucide-react";
import { getSession } from "@/modules/auth/session";
import { SITE } from "@/shared/config/site";
import { LoginForm } from "@/modules/auth/login-form";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export const metadata: Metadata = { title: "登录" };

export default async function LoginPage() {
  // If already authenticated, skip the form.
  if (await getSession()) redirect("/overview");

  return (
    <main className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 grid-fade opacity-40" />
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex size-12 items-center justify-center rounded-xl bg-primary-muted text-primary ring-1 ring-primary/30">
            <Crosshair className="size-6" />
          </span>
          <h1 className="text-lg font-semibold tracking-tight text-fg">
            {SITE.name}
          </h1>
          <p className="mt-1 text-sm text-fg-muted">{SITE.tagline}</p>
        </div>

        <Card className="p-6">
          <Suspense fallback={<LoginFormSkeleton />}>
            <LoginForm />
          </Suspense>
        </Card>

        <p className="mt-6 text-center text-xs text-fg-subtle">
          {SITE.fullName}
        </p>
      </div>
    </main>
  );
}

function LoginFormSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-11 w-full" />
    </div>
  );
}
