"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Fingerprint, LoaderCircle, Pencil, Trash2 } from "lucide-react";
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
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { PASSKEYS_PROXY_PATH } from "@/modules/profile/passkey-upstream";
import type { PasskeyItem } from "@/modules/profile/types";
import {
  creationOptionsFromJson,
  credentialToJson,
  extractPasskeyDetail,
  isPasskeyErrorCode,
  isPasskeySupported,
  passkeyEnvironmentIssue,
} from "@/modules/auth/webauthn";

type JsonRecord = Record<string, unknown>;

export function PasskeyForm({
  initial,
  loadError = false,
}: {
  initial: readonly PasskeyItem[];
  loadError?: boolean;
}) {
  const t = useTranslations("profile");
  const router = useRouter();
  const [items, setItems] = useState<PasskeyItem[]>(() => [...initial]);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(loadError ? t("passkeyLoadError") : null);
  const [renameId, setRenameId] = useState<number | null>(null);
  const [nickname, setNickname] = useState("");
  const env = passkeyEnvironmentIssue();

  function mapError(detail: string | null, status: number) {
      if (detail && isPasskeyErrorCode(detail)) return t(detail);
    if (detail === "NotAllowedError") return t("passkeyCancelled");
    return t("passkeyFailed", { status });
  }

  async function bindPasskey() {
    if (env === "ip") {
      setBanner(t("passkey_ip_origin"));
      return;
    }
    if (env === "insecure") {
      setBanner(t("passkey_insecure_origin"));
      return;
    }
    if (!isPasskeySupported()) {
      setBanner(t("passkeyUnavailable"));
      return;
    }
    setPending("bind");
    setBanner(null);
    try {
      const optionsResponse = await fetch(`${PASSKEYS_PROXY_PATH}?action=register-options`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!optionsResponse.ok) {
        setBanner(mapError(await extractPasskeyDetail(optionsResponse), optionsResponse.status));
        return;
      }
      const payload = (await optionsResponse.json()) as { public_key?: JsonRecord };
      if (!payload.public_key) {
        setBanner(t("passkeyFailed", { status: optionsResponse.status }));
        return;
      }
      const credential = await navigator.credentials.create({
        publicKey: creationOptionsFromJson(payload.public_key),
      });
      if (!(credential instanceof PublicKeyCredential)) {
        setBanner(t("passkeyCancelled"));
        return;
      }
      const verifyResponse = await fetch(`${PASSKEYS_PROXY_PATH}?action=register-verify`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ credential: credentialToJson(credential) }),
      });
      if (!verifyResponse.ok) {
        setBanner(mapError(await extractPasskeyDetail(verifyResponse), verifyResponse.status));
        return;
      }
      const created = toItem((await verifyResponse.json()) as WirePasskey);
      setItems((current) => [...current, created]);
      setRenameId(created.id);
      setNickname("");
      setBanner(t("passkeyAdded"));
      router.refresh();
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "NotAllowedError") {
        setBanner(t("passkeyCancelled"));
      } else {
        setBanner(t("networkError"));
      }
    } finally {
      setPending(null);
    }
  }

  async function saveNickname() {
    if (renameId == null) return;
    const trimmed = nickname.trim();
    if (!trimmed) {
      setRenameId(null);
      return;
    }
    setPending("rename");
    try {
      const response = await fetch(`${PASSKEYS_PROXY_PATH}?id=${renameId}`, {
        method: "PATCH",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ nickname: trimmed }),
      });
      if (!response.ok) {
        setBanner(mapError(await extractPasskeyDetail(response), response.status));
        return;
      }
      const updated = toItem((await response.json()) as WirePasskey);
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setBanner(t("passkeyRenamed"));
      setRenameId(null);
      router.refresh();
    } catch {
      setBanner(t("networkError"));
    } finally {
      setPending(null);
    }
  }

  async function removePasskey(item: PasskeyItem) {
    if (!(await confirm(t("passkeyDeleteConfirm")))) return;
    setPending(`delete-${item.id}`);
    setBanner(null);
    try {
      const response = await fetch(`${PASSKEYS_PROXY_PATH}?id=${item.id}`, {
        method: "DELETE",
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        setBanner(mapError(await extractPasskeyDetail(response), response.status));
        return;
      }
      setItems((current) => current.filter((row) => row.id !== item.id));
      setBanner(t("passkeyRemoved"));
      router.refresh();
    } catch {
      setBanner(t("networkError"));
    } finally {
      setPending(null);
    }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{t("passkeyTitle")}</CardTitle>
            <CardDescription>{t("passkeyHelp")}</CardDescription>
          </div>
          <Badge tone={items.length > 0 ? "ok" : "neutral"}>
            {items.length > 0 ? t("configured") : t("notConfigured")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {env === "ip" ? (
          <p className="text-sm text-warn">{t("passkey_ip_origin")}</p>
        ) : null}
        {env === "insecure" ? (
          <p className="text-sm text-warn">{t("passkey_insecure_origin")}</p>
        ) : null}
        {items.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("passkeyEmpty")}</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-start justify-between gap-3 rounded-md border border-line bg-surface-raised/40 px-4 py-3"
              >
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm font-medium text-fg">
                    {item.nickname.trim() || t("passkeyUnnamed")}
                  </p>
                  <p className="text-xs text-fg-muted">
                    {item.lastUsedAt
                      ? t("passkeyLastUsed", { time: formatStamp(item.lastUsedAt) })
                      : t("passkeyNeverUsed")}
                  </p>
                  {item.backupEligible ? (
                    <p className="text-xs text-fg-subtle">{t("passkeySynced")}</p>
                  ) : null}
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={Boolean(pending)}
                    aria-label={t("passkeyRename")}
                    onClick={() => {
                      setRenameId(item.id);
                      setNickname(item.nickname);
                    }}
                  >
                    <Pencil />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={Boolean(pending)}
                    aria-label={t("passkeyDelete")}
                    onClick={() => void removePasskey(item)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}
        <Button
          type="button"
          disabled={Boolean(pending) || env !== null}
          onClick={() => void bindPasskey()}
        >
          {pending === "bind" ? <LoaderCircle className="animate-spin" /> : <Fingerprint />}
          {pending === "bind" ? t("passkeyBinding") : t("passkeyBind")}
        </Button>
      </CardContent>
      <Dialog
        open={renameId != null}
        title={t("passkeyRenameTitle")}
        description={t("passkeyRenameHelp")}
        closeLabel={t("passkeyRenameCancel")}
        onClose={() => {
          if (pending === "rename") return;
          setRenameId(null);
        }}
        className="max-w-md"
        footer={
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={pending === "rename"}
              onClick={() => setRenameId(null)}
            >
              {t("passkeyRenameCancel")}
            </Button>
            <Button type="button" disabled={pending === "rename"} onClick={() => void saveNickname()}>
              {pending === "rename" ? t("saving") : t("passkeyNicknameSave")}
            </Button>
          </div>
        }
      >
        <div>
          <Label htmlFor="passkey-nickname">{t("passkeyNickname")}</Label>
          <Input
            id="passkey-nickname"
            maxLength={100}
            value={nickname}
            onChange={(event) => setNickname(event.target.value)}
          />
        </div>
      </Dialog>
    </Card>
  );
}

type WirePasskey = {
  id: number;
  nickname?: string;
  transports?: string[];
  created_at?: string | null;
  last_used_at?: string | null;
  backup_eligible?: boolean | null;
  backup_state?: boolean | null;
};

function toItem(raw: WirePasskey): PasskeyItem {
  return {
    id: raw.id,
    nickname: raw.nickname ?? "",
    transports: raw.transports ?? [],
    createdAt: raw.created_at ?? null,
    lastUsedAt: raw.last_used_at ?? null,
    backupEligible: raw.backup_eligible ?? null,
    backupState: raw.backup_state ?? null,
  };
}

function formatStamp(value: string): string {
  return value.slice(0, 19).replace("T", " ");
}
