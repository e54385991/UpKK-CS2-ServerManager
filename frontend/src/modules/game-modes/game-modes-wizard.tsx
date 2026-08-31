"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  TriangleAlert,
  ListChecks,
  Gamepad2,
} from "lucide-react";
import {
  installGameModeAction,
  preflightGameModeAction,
} from "@/modules/game-modes/actions";
import type {
  GameModeCatalog,
  GameModeMutation,
  GameModePlan,
  GameModeSummary,
} from "@/modules/game-modes/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { workspaceHref } from "@/modules/servers/workspace";
import { notify } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { LinkButton } from "@/shared/ui/link-button";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

function formatValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function presenceTone(value: boolean | null): "ok" | "warn" | "neutral" {
  if (value === true) return "ok";
  if (value === false) return "warn";
  return "neutral";
}

export function GameModesWizard({
  catalog,
  serverName,
}: {
  catalog: GameModeCatalog;
  serverName: string;
}) {
  const t = useTranslations("gameModes");
  const kz = catalog.modes.find((item) => item.id === "kz") ?? catalog.modes[0];
  const [wipeWanted, setWipeWanted] = useState(false);
  const [wipeAck, setWipeAck] = useState(false);
  const [plan, setPlan] = useState<GameModePlan | null>(null);
  const [pending, setPending] = useState<"preview" | "install" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  const wipeEnabled = wipeWanted && wipeAck;

  function resetPlan() {
    setPlan(null);
    setQueued(false);
  }

  async function preview() {
    if (!kz) return;
    if (wipeWanted && !wipeAck) {
      setError(t("wipeRequired"));
      return;
    }
    setPending("preview");
    setError(null);
    setQueued(false);
    const result = await preflightGameModeAction(catalog.serverId, kz.id, wipeEnabled);
    setPending(null);
    if (!result.ok) {
      setPlan(null);
      setError(result.error || t("fetchError", { status: result.status || "network" }));
      return;
    }
    setPlan(result.data);
  }

  async function install() {
    if (!kz || !plan || plan.blocked) return;
    if (wipeEnabled && !wipeAck) {
      setError(t("wipeRequired"));
      return;
    }
    setPending("install");
    setError(null);
    const result = await installGameModeAction(catalog.serverId, kz.id, {
      wipeAddons: wipeEnabled,
      wipeAddonsAcknowledged: wipeEnabled,
      planHash: plan.planHash,
      acknowledgeWarningRuleIds: plan.warnings.map((item) => item.ruleId),
    });
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("fetchError", { status: result.status || "network" }));
      return;
    }
    trackQueuedOperation(result.data, {
      serverName,
      latestMessage: result.data.message,
    });
    notify.info(t("queued"));
    setQueued(true);
  }

  if (!kz) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: "empty" })}
      </Card>
    );
  }

  return (
    <div className="space-y-6" data-testid="game-modes-wizard">
      {!catalog.reachable ? (
        <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          <TriangleAlert className="size-4 shrink-0" />
          <span>{t("unreachable")}</span>
        </Card>
      ) : null}

      <ModeCard mode={kz} />

      <Card>
        <CardHeader>
          <CardTitle>{t("stepsTitle")}</CardTitle>
          <CardDescription>{t("previewHelp")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="space-y-2 text-sm text-fg-muted">
            {(
              [
                "stepFramework",
                "stepKz",
                "stepChooser",
                "stepRestart",
                "stepConfig",
                "stepMap",
              ] as const
            ).map((key, index) => (
              <li key={key} className="flex gap-3">
                <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-surface-raised text-xs font-semibold text-fg">
                  {index + 1}
                </span>
                <span>{t(key)}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <Card data-testid="game-modes-wipe" className={cn(wipeWanted && "border-danger/40")}>
        <CardHeader>
          <CardTitle>{t("wipeTitle")}</CardTitle>
          <CardDescription>{t("wipeHelp")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Switch
            id="wipe-addons"
            label={t("wipeTitle")}
            checked={wipeWanted}
            disabled={pending != null}
            onCheckedChange={(next) => {
              setWipeWanted(next);
              if (!next) setWipeAck(false);
              resetPlan();
            }}
          />
          {wipeWanted ? (
            <div className="rounded-md border border-danger/30 bg-danger-muted/30 px-4 py-3 text-sm text-danger">
              <p className="flex items-start gap-2">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                <span>{t("wipeWarning", { path: catalog.addonsPath })}</span>
              </p>
              <label className="mt-3 flex items-start gap-2 text-fg">
                <input
                  data-testid="game-modes-wipe-ack"
                  type="checkbox"
                  className="mt-1 size-4 accent-danger"
                  checked={wipeAck}
                  disabled={pending != null}
                  onChange={(event) => {
                    setWipeAck(event.target.checked);
                    resetPlan();
                  }}
                />
                <span>{t("wipeAck")}</span>
              </label>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          data-testid="game-modes-preflight"
          type="button"
          onClick={() => {
            void preview();
          }}
          disabled={pending != null || (wipeWanted && !wipeAck)}
        >
          <ListChecks className="size-4" />
          {pending === "preview" ? t("previewing") : t("preview")}
        </Button>
        {plan && !plan.blocked ? (
          <Button
            data-testid="game-modes-install"
            type="button"
            variant={wipeEnabled ? "danger" : "primary"}
            onClick={() => {
              void install();
            }}
            disabled={pending != null || queued}
          >
            {pending === "install" ? t("starting") : t("start")}
          </Button>
        ) : null}
        {queued ? (
          <LinkButton
            href={workspaceHref(catalog.serverId, "operations")}
            variant="outline"
            size="sm"
          >
            {t("openOperations")}
          </LinkButton>
        ) : null}
      </div>

      {error ? (
        <Card className="border-danger/30 bg-danger-muted/30 px-5 py-4 text-sm text-danger">
          {error}
        </Card>
      ) : null}

      {plan ? <PlanCard plan={plan} /> : null}
    </div>
  );
}

function ModeCard({ mode }: { mode: GameModeSummary }) {
  const t = useTranslations("gameModes");
  const launch = Object.entries(mode.launchUpsert)
    .map(([key, value]) => `${key} ${value}`)
    .join(" ");
  return (
    <Card data-testid="game-modes-kz">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Gamepad2 className="size-4 text-primary" />
          <CardTitle>{t("kzName")}</CardTitle>
        </div>
        <CardDescription>{t("kzSummary")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-fg-muted">{t("kzPurpose")}</p>
        <section>
          <h3 className="mb-2 font-medium text-fg">{t("stackTitle")}</h3>
          <ul className="flex flex-wrap gap-2">
            <PresenceChip
              label="CounterStrikeSharp"
              value={mode.present.counterstrikesharp}
            />
            <PresenceChip label="cs2kz-metamod" value={mode.present.cs2kzMetamod} />
            <PresenceChip
              label="CS2-Upkk-PanelPLG-Mapchooser"
              value={mode.present.mapchooser}
            />
          </ul>
        </section>
        <section>
          <h3 className="mb-1 font-medium text-fg">{t("launchTitle")}</h3>
          <p className="font-mono text-xs text-fg-muted">{launch}</p>
        </section>
        <section>
          <h3 className="mb-1 font-medium text-fg">{t("mapsTitle")}</h3>
          <p className="text-fg-muted">
            {t("startupMap", { id: mode.startupWorkshopMap })}
          </p>
          {mode.maps.map((item) => (
            <p key={item.workshopId} className="text-fg-muted">
              {t("poolMap", { name: item.name, id: item.workshopId })}
            </p>
          ))}
        </section>
        <section>
          <h3 className="mb-1 font-medium text-fg">{t("configTitle")}</h3>
          <ul className="space-y-1 text-fg-muted">
            <li>{t("configUseGameTimeLimit")}</li>
            <li>{t("configEnforceTimeLimit")}</li>
            <li>{t("configHostWorkshop")}</li>
          </ul>
        </section>
        {mode.missingMarketPlugins.length > 0 ? (
          <p className="text-danger">
            {t("missingMarket", {
              titles: mode.missingMarketPlugins.join(", "),
            })}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PresenceChip({
  label,
  value,
}: {
  label: string;
  value: boolean | null;
}) {
  const t = useTranslations("gameModes");
  const status =
    value === true ? t("present") : value === false ? t("absent") : t("unknown");
  return (
    <Badge tone={presenceTone(value)}>
      {label}: {status}
    </Badge>
  );
}

function PlanCard({ plan }: { plan: GameModePlan }) {
  const t = useTranslations("gameModes");
  return (
    <Card data-testid="game-modes-mutations">
      <CardHeader>
        <CardTitle>{t("mutationsTitle")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {plan.blocked ? (
          <div className="rounded-md border border-danger/30 bg-danger-muted/30 px-4 py-3 text-sm text-danger">
            <p className="font-medium">{t("blocked")}</p>
            <ul className="mt-1 list-disc pl-5">
              {plan.blockingReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {plan.warnings.map((warning) => (
          <p key={warning.ruleId} className="text-sm text-warn">
            {warning.reason}
          </p>
        ))}
        <ul className="space-y-3">
          {plan.mutations.map((item) => (
            <MutationRow key={item.id} item={item} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function MutationRow({ item }: { item: GameModeMutation }) {
  const t = useTranslations("gameModes");
  const statusLabel =
    item.status === "unchanged"
      ? t("mutationUnchanged")
      : item.status === "already_present"
        ? t("mutationPresent")
        : t("mutationPending");
  return (
    <li
      className={cn(
        "rounded-md border px-4 py-3",
        item.destructive
          ? "border-danger/40 bg-danger-muted/20"
          : "border-line bg-surface-raised/40",
      )}
    >
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <p className="font-medium text-fg">{item.target}</p>
        <Badge tone={item.destructive ? "danger" : item.status === "pending" ? "info" : "neutral"}>
          {item.destructive ? t("destructive") : statusLabel}
        </Badge>
      </div>
      <p className="font-mono text-xs text-fg-muted">
        {formatValue(item.before)} → {formatValue(item.after)}
      </p>
    </li>
  );
}
