/**
 * Ergonomic aliases over the generated OpenAPI schema. Import domain types from
 * here so call sites never reach into the raw `components["schemas"][...]`
 * shape. Regenerate the underlying schema with `npm run gen:api`.
 */
import type { components, paths } from "@/shared/api/schema";

export type Schemas = components["schemas"];
export type Paths = paths;

export type SessionUserDto = Schemas["SessionUser"];
export type ServerSummaryDto = Schemas["ServerSummary"];
export type ServerDetailDto = Schemas["ServerDetail"];
export type OverviewSummaryDto = Schemas["OverviewSummary"];
export type ServerStatusDto = Schemas["ServerStatus"];
