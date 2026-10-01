"use client";

import {
  applySystemCleanupAction,
  deleteCleanupAction,
  updateCleanupPolicyAction,
} from "@/modules/cleanup/actions";
import {
  cleanupScanStreamUrl,
  cleanupSystemStreamUrl,
  openCleanupEventSource,
} from "@/modules/cleanup/stream";
import {
  type CleanupPolicy,
  type CleanupScan,
  type CleanupSystemScan
} from "@/modules/cleanup/types";
import {
  toCleanupScan,
  toCleanupSystemScan,
  type CleanupScanViewDto,
  type CleanupSystemScanDto,
} from "@/modules/cleanup/wire";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import { confirm } from "@/shared/feedback";
import type { Route } from "next";
import { useFormatter, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

export function useCleanupConsole({
  serverId,
  initialPolicy,
}: {
  serverId: number;
  initialPolicy: CleanupPolicy | null;
}) {
  const t = useTranslations("cleanup");
  const format = useFormatter();
  const [scan, setScan] = useState<CleanupScan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [workshopConfirm, setWorkshopConfirm] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [queuedOperationId, setQueuedOperationId] = useState<string | null>(null);
  const queuedKindRef = useRef<"delete" | "system" | null>(null);
  const [scanProgress, setScanProgress] = useState<string | null>(null);
  const streamCancelRef = useRef<(() => void) | null>(null);
  const [systemScan, setSystemScan] = useState<CleanupSystemScan | null>(null);
  const [systemSelected, setSystemSelected] = useState<string[]>([]);
  const [policy, setPolicy] = useState<CleanupPolicy | null>(initialPolicy);
  const [policyEnabled, setPolicyEnabled] = useState(initialPolicy?.enabled ?? false);
  const [retainDays, setRetainDays] = useState(String(initialPolicy?.retainDays ?? 7));
  const [scheduleValue, setScheduleValue] = useState(
    initialPolicy?.scheduleValue ?? "03:30",
  );
  const [policyTargets, setPolicyTargets] = useState<string[]>(
    [...(initialPolicy?.targets ?? ["game_logs"])],
  );
  const hostConfigHref = `/servers/${serverId}/host-config` as Route;
  const queued = Boolean(queuedOperationId);

  function closeStream() {
    streamCancelRef.current?.();
    streamCancelRef.current = null;
  }

  useEffect(() => {
    return () => {
      streamCancelRef.current?.();
      streamCancelRef.current = null;
    };
  }, []);

  useQueuedOperationTerminal(queuedOperationId, serverId, (status, message) => {
    const kind = queuedKindRef.current;
    queuedKindRef.current = null;
    setQueuedOperationId(null);
    setBanner(message || (status === "completed" ? t("queuedDone") : t("failed")));
    if (status !== "completed") return;
    if (kind === "delete") {
      setScan(null);
      setSelected([]);
    } else if (kind === "system") {
      setSystemScan(null);
      setSystemSelected([]);
    }
  });

  function phaseText(phase: unknown, fallback: string): string {
    const known = {
      safe_roots: t("phase.safe_roots"),
      logs: t("phase.logs"),
      archives: t("phase.archives"),
      workshop: t("phase.workshop"),
      privilege: t("phase.privilege"),
      game_logs: t("phase.game_logs"),
      thumbnails: t("phase.thumbnails"),
      apt_cache: t("phase.apt_cache"),
      journal: t("phase.journal"),
      tmp: t("phase.tmp"),
      crash: t("phase.crash"),
      rotated_logs: t("phase.rotated_logs"),
    } as const;
    if (typeof phase === "string" && phase in known) {
      return known[phase as keyof typeof known];
    }
    return fallback || t("scanning");
  }

  function runScan() {
    closeStream();
    setPending("scan");
    setBanner(null);
    setScan(null);
    setSelected([]);
    setScanProgress(t("scanning"));
    streamCancelRef.current = openCleanupEventSource(cleanupScanStreamUrl(serverId), {
      streamFailedMessage: t("streamFailed"),
      streamClosedMessage: t("streamClosed"),
      onPhase: (message) => {
        setScanProgress(message || t("scanning"));
      },
      onEvent: (type, data) => {
        if (type === "phase") {
          setScanProgress(phaseText(data.phase, typeof data.message === "string" ? data.message : ""));
          return;
        }
        if (type === "batch") {
          const found = Number(data.found) || 0;
          const categories = {
            safe: t("category.safe"),
            archive: t("category.archive"),
            workshop: t("category.workshop"),
          } as const;
          const category =
            typeof data.category === "string" && data.category in categories
              ? categories[data.category as keyof typeof categories]
              : "";
          setScanProgress(
            `${phaseText(data.phase, "")}${category ? ` · ${category}` : ""} · ${t("scanFound", { found })}`,
          );
        }
      },
      onDone: (data) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setScan(toCleanupScan(data as CleanupScanViewDto));
        setSelected([]);
      },
      onError: (message) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setBanner(message || t("failed"));
      },
    });
  }

  function toggleArchive(path: string, checked: boolean) {
    setSelected((current) =>
      checked ? [...current, path] : current.filter((item) => item !== path),
    );
  }

  async function removeSafe() {
    if (!(await confirm(t("confirmSafe")))) return;
    setPending("safe");
    const result = await deleteCleanupAction(serverId, { mode: "safe" });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  async function removeArchives() {
    if (selected.length === 0) return;
    if (!(await confirm(t("confirmArchives")))) return;
    setPending("archives");
    const result = await deleteCleanupAction(serverId, {
      mode: "archives",
      paths: selected,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  async function removeWorkshop() {
    if (!(await confirm(t("confirmWorkshop")))) return;
    setPending("workshop");
    const result = await deleteCleanupAction(serverId, {
      mode: "workshop",
      confirmationText: workshopConfirm,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  function runSystemScan() {
    closeStream();
    setPending("system-scan");
    setBanner(null);
    setSystemScan(null);
    setScanProgress(t("systemScanning"));
    streamCancelRef.current = openCleanupEventSource(cleanupSystemStreamUrl(serverId), {
      streamFailedMessage: t("streamFailed"),
      streamClosedMessage: t("streamClosed"),
      onPhase: (message) => {
        setScanProgress(message || t("systemScanning"));
      },
      onEvent: (type, data) => {
        if (type === "phase") {
          setScanProgress(phaseText(data.phase, typeof data.message === "string" ? data.message : ""));
        }
      },
      onDone: (data) => {
        const next = toCleanupSystemScan(data as CleanupSystemScanDto);
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setSystemScan(next);
        setSystemSelected(next.targets.filter((item) => item.canApply).map((item) => item.id));
      },
      onError: (message) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setBanner(message || t("failed"));
      },
    });
  }

  function toggleSystem(id: string, checked: boolean) {
    setSystemSelected((current) =>
      checked ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  async function cleanSystem() {
    if (systemSelected.length === 0) return;
    if (!(await confirm(t("confirmSystem")))) return;
    setPending("system-clean");
    const result = await applySystemCleanupAction(serverId, {
      targets: systemSelected,
      retainDays: Number(retainDays) || 7,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "system";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  function togglePolicyTarget(id: string, checked: boolean) {
    setPolicyTargets((current) =>
      checked ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  async function savePolicy() {
    setPending("policy");
    setBanner(null);
    const result = await updateCleanupPolicyAction(serverId, {
      enabled: policyEnabled,
      retainDays: Number(retainDays) || 7,
      scheduleValue,
      targets: policyTargets,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setPolicy(result.data);
    setBanner(result.data.message || t("policySaved"));
  }

  const privilegeTone: "danger" | "ok" | "neutral" =
    systemScan?.privilege === "none"
      ? "danger"
      : systemScan?.privilege === "sudo" || systemScan?.privilege === "root"
        ? "ok"
        : "neutral";

  return {
    t,
    format,
    scan,
    selected,
    workshopConfirm,
    setWorkshopConfirm,
    pending,
    banner,
    scanProgress,
    systemScan,
    systemSelected,
    policy,
    policyEnabled,
    setPolicyEnabled,
    retainDays,
    setRetainDays,
    scheduleValue,
    setScheduleValue,
    policyTargets,
    hostConfigHref,
    queued,
    runScan,
    toggleArchive,
    removeSafe,
    removeArchives,
    removeWorkshop,
    runSystemScan,
    toggleSystem,
    cleanSystem,
    togglePolicyTarget,
    savePolicy,
    privilegeTone,
  };
}
