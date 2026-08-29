import type { ServerOperationAction } from "@/modules/servers/types";

export const FRAMEWORK_IDS = [
  "metamod",
  "counterstrikesharp",
  "cs2fixes",
  "swiftly",
] as const;

export type FrameworkId = (typeof FRAMEWORK_IDS)[number];

export type FrameworkRole =
  | "required"
  | "recommended"
  | "optional"
  | "alternative";

export type FrameworkSpec = {
  readonly id: FrameworkId;
  readonly install: ServerOperationAction;
  readonly update: ServerOperationAction;
  readonly role: FrameworkRole;
  readonly dependsOn: readonly FrameworkId[];
  readonly conflictsWith: readonly FrameworkId[];
};

/**
 * Install order for the Metamod / CounterStrikeSharp stack. SwiftlyS2 is a
 * separate runtime and is kept off this path on purpose.
 */
export const FRAMEWORK_CATALOG: readonly FrameworkSpec[] = [
  {
    id: "metamod",
    install: "install_metamod",
    update: "update_metamod",
    role: "required",
    dependsOn: [],
    conflictsWith: [],
  },
  {
    id: "counterstrikesharp",
    install: "install_counterstrikesharp",
    update: "update_counterstrikesharp",
    role: "recommended",
    dependsOn: ["metamod"],
    conflictsWith: ["swiftly"],
  },
  {
    id: "cs2fixes",
    install: "install_cs2fixes",
    update: "update_cs2fixes",
    role: "optional",
    dependsOn: ["metamod"],
    conflictsWith: ["swiftly"],
  },
  {
    id: "swiftly",
    install: "install_swiftly",
    update: "update_swiftly",
    role: "alternative",
    dependsOn: [],
    conflictsWith: ["counterstrikesharp", "cs2fixes"],
  },
];

const ROLE_TONE: Record<
  FrameworkRole,
  "danger" | "ok" | "info" | "warn"
> = {
  required: "danger",
  recommended: "ok",
  optional: "info",
  alternative: "warn",
};

export function frameworkRoleTone(role: FrameworkRole) {
  return ROLE_TONE[role];
}

type FrameworkHint = {
  readonly frameworkKey?: string | null;
  readonly sourceKey?: string | null;
  readonly displayName?: string | null;
};

function matchesFramework(hint: string, id: FrameworkId): boolean {
  const value = hint.trim().toLowerCase();
  if (!value) return false;
  if (id === "metamod") return value === "metamod" || value.includes("metamod");
  if (id === "counterstrikesharp") {
    return value === "counterstrikesharp" || value.includes("counterstrikesharp");
  }
  if (id === "cs2fixes") return value.includes("cs2fixes");
  return value.includes("swiftly");
}

export function detectInstalledFrameworkKeys(
  plugins: readonly FrameworkHint[],
): FrameworkId[] {
  const found = new Set<FrameworkId>();
  for (const plugin of plugins) {
    const hints = [
      plugin.frameworkKey,
      plugin.sourceKey,
      plugin.displayName,
    ];
    for (const id of FRAMEWORK_IDS) {
      if (hints.some((hint) => hint != null && matchesFramework(hint, id))) {
        found.add(id);
      }
    }
  }
  return FRAMEWORK_IDS.filter((id) => found.has(id));
}
