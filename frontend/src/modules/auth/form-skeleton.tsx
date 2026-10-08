import { Skeleton } from "@/shared/ui/skeleton";

export function AuthFormSkeleton() {
  return <div className="space-y-4" data-testid="auth-form-skeleton">
    <Skeleton className="h-16 w-full" />
    <Skeleton className="h-16 w-full" />
    <Skeleton className="h-16 w-full" />
    <Skeleton className="h-11 w-full" />
  </div>;
}
