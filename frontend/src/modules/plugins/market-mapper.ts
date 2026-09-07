import type { MarketPluginViewDto } from "@/shared/api/types";
import type { MarketPlugin, PluginRef } from "@/modules/plugins/types";
import { toPluginFramework } from "@/modules/plugins/types";

export function toRef(raw: { id: number; title: string }): PluginRef {
  return { id: raw.id, title: raw.title };
}

export function toMarketPlugin(raw: MarketPluginViewDto): MarketPlugin {
  return {
    aiMetadata: raw.ai_metadata ?? null,
    id: raw.id,
    title: raw.title,
    description: raw.description ?? null,
    author: raw.author ?? null,
    version: raw.version ?? null,
    category: raw.category,
    framework: toPluginFramework(raw.framework),
    tags: raw.tags ?? null,
    isRecommended: raw.is_recommended,
    iconUrl: raw.icon_url ?? null,
    githubUrl: raw.github_url,
    customInstallPath: raw.custom_install_path ?? null,
    downloadCount: raw.download_count,
    installCount: raw.install_count,
    createdAt: raw.created_at ?? null,
    dependencies: (raw.dependencies ?? []).map(toRef),
  };
}
