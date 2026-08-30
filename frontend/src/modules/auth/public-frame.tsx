import { getTranslations } from "next-intl/server";
import { Crosshair } from "lucide-react";
import { Card } from "@/shared/ui/card";

export async function PublicAuthFrame({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = await getTranslations("site");

  return (
    <main className="relative flex flex-1 items-center justify-center px-4 py-12">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 grid-fade opacity-40" />
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex size-12 items-center justify-center rounded-xl bg-primary-muted text-primary ring-1 ring-primary/30">
            <Crosshair className="size-6" />
          </span>
          <h1 className="text-lg font-semibold tracking-tight text-fg">
            {t("name")}
          </h1>
          <p className="mt-1 text-sm text-fg-muted">{t("tagline")}</p>
        </div>

        <Card className="p-6">{children}</Card>
      </div>
    </main>
  );
}
