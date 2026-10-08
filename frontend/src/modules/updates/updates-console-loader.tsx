"use client";
import dynamic from "next/dynamic";
import { Skeleton } from "@/shared/ui/skeleton";

function UpdatesSkeleton() {
  return <div className="space-y-3 rounded-lg border border-line bg-surface p-5 shadow-panel"><Skeleton className="h-4 w-40" /><Skeleton className="h-10 w-full" /></div>;
}
export const UpdatesConsole = dynamic(() => import("./updates-console").then(module => module.UpdatesConsole), { ssr: false, loading: UpdatesSkeleton });
