import type { PluginFrameworkCompatibility } from "@/modules/plugins/types";

/**
 * Message values for the "this server runs X, so Y plugins do not load"
 * warning. Pure on purpose: the caller owns the translator, so this stays
 * testable and there is no hook indirection between the two call sites (the
 * install form's confirm dialog and the plan summary banner).
 */
export function runtimeMismatchValues(
  framework: PluginFrameworkCompatibility,
  label: (key: string) => string,
): { readonly server: string; readonly plugin: string } {
  return {
    server: framework.conflicting.map(label).join(", "),
    plugin: label(framework.plugin),
  };
}
