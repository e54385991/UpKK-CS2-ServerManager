import "server-only";
import { cache } from "react";
import { listServers } from "@/modules/servers/api";

/** Authorized installer choices are deduplicated only within an RSC render. */
export const listInstallServers = cache(listServers);
