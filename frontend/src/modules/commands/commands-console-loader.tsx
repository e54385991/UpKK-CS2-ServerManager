"use client";
import dynamic from "next/dynamic";
import { Skeleton } from "@/shared/ui/skeleton";

function CommandsSkeleton() {
  return <div className="space-y-3 rounded-lg border border-line bg-surface p-5"><Skeleton className="h-4 w-40" /><Skeleton className="h-12 w-full" /></div>;
}
export const CommandsConsole = dynamic(() => import("./commands-console").then(module => module.CommandsConsole), { ssr: false, loading: CommandsSkeleton });
