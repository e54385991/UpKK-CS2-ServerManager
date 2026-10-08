import "server-only";
import { revalidatePath } from "next/cache";
import { internalLocalePaths } from "./paths";
import type { PublicPath } from "./public-route";

export function revalidatePublicPath(path: PublicPath, type?: "page" | "layout") {
  for (const internal of internalLocalePaths(path)) revalidatePath(internal, type);
}
