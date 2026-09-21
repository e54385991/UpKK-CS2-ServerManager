"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LoaderCircle, LogIn } from "lucide-react";
import {
  googleIdTokenFromMessage,
  openGoogleIdTokenPopup,
} from "@/modules/auth/google-popup";
import {
  bindGoogleAction,
  unbindGoogleAction,
} from "@/modules/profile/actions";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

const GOOGLE_ERRORS = [
  "google_not_configured",
  "google_invalid_token",
  "google_email_unverified",
  "google_account_taken",
  "google_already_linked",
] as const;

function isGoogleError(value: string): value is (typeof GOOGLE_ERRORS)[number] {
  return (GOOGLE_ERRORS as readonly string[]).includes(value);
}

export function GoogleLinkForm({ initialLinked }: { initialLinked: boolean }) {
  const t = useTranslations("profile");
  const router = useRouter();
  const [linked, setLinked] = useState(initialLinked);
  const [clientId, setClientId] = useState("");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [pending, setPending] = useState<"bind" | "unbind" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void fetch("/api/v1/auth/google-config")
      .then(async (response) => (response.ok ? response.json() : null))
      .then((data: { client_id?: unknown; enabled?: unknown } | null) => {
        if (!active) return;
        const nextId = typeof data?.client_id === "string" ? data.client_id : "";
        setClientId(nextId);
        setEnabled(Boolean(data?.enabled && nextId));
      })
      .catch(() => {
        if (active) setEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const bind = useCallback(
    async (idToken: string) => {
      setPending("bind");
      setBanner(null);
      const result = await bindGoogleAction(idToken);
      setPending(null);
      if (!result.ok) {
        setBanner(
          isGoogleError(result.error)
            ? t(result.error)
            : t("googleFailed", { status: result.status || "network" }),
        );
        return;
      }
      setLinked(result.data.googleLinked);
      setBanner(t("googleBound"));
      router.refresh();
    },
    [router, t],
  );

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      const idToken = googleIdTokenFromMessage(event.data);
      if (!idToken) return;
      void bind(idToken);
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [bind]);

  function startBind() {
    if (!clientId) return;
    const popup = openGoogleIdTokenPopup(clientId);
    if (!popup) {
      setBanner(t("googlePopupBlocked"));
      return;
    }
    setBanner(null);
  }

  async function unbind() {
    if (!(await confirm(t("googleUnbindConfirm")))) return;
    setPending("unbind");
    setBanner(null);
    const result = await unbindGoogleAction();
    setPending(null);
    if (!result.ok) {
      setBanner(t("googleFailed", { status: result.status || "network" }));
      return;
    }
    setLinked(false);
    setBanner(t("googleUnbound"));
    router.refresh();
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{t("googleTitle")}</CardTitle>
            <CardDescription>{t("googleHelp")}</CardDescription>
          </div>
          <Badge tone={linked ? "ok" : "neutral"}>
            {linked ? t("googleLinked") : t("googleUnlinked")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {enabled === false ? <p className="text-sm text-warn">{t("googleUnavailable")}</p> : null}
        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}
        {linked ? (
          <Button
            type="button"
            variant="outline"
            disabled={pending !== null}
            onClick={() => void unbind()}
          >
            {pending === "unbind" ? <LoaderCircle className="animate-spin" /> : null}
            {pending === "unbind" ? t("googleUnbinding") : t("googleUnbind")}
          </Button>
        ) : (
          <Button
            type="button"
            disabled={pending !== null || enabled !== true}
            onClick={startBind}
          >
            {pending === "bind" ? <LoaderCircle className="animate-spin" /> : <LogIn />}
            {pending === "bind" ? t("googleBinding") : t("googleBind")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
