import "server-only";
import { cache } from "react";
import {
  getServer as loadServer,
  getCurrentServerOperation as loadCurrentOperation,
  getDeploymentLock as loadDeploymentLock,
} from "@/modules/servers/api";

// React discards these snapshots after each RSC render. Keep the underlying
// timeout and no-store fetch; never use these wrappers for mutations or polling.
export const getServer = cache(loadServer);
export const getCurrentServerOperation = cache(loadCurrentOperation);
export const getDeploymentLock = cache(loadDeploymentLock);
