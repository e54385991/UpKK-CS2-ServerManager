"use client";
import dynamic from "next/dynamic";
import { Skeleton } from "@/shared/ui/skeleton";

function FormSkeleton() {
  return <div className="grid gap-6 xl:grid-cols-2"><Skeleton className="h-80 rounded-lg" /><Skeleton className="h-80 rounded-lg" /></div>;
}
export const SetupWizard = dynamic(() => import("./setup-wizard").then(module => module.SetupWizard), { ssr: false, loading: FormSkeleton });
export const CloneServerForm = dynamic(() => import("./clone-form").then(module => module.CloneServerForm), { ssr: false, loading: FormSkeleton });
