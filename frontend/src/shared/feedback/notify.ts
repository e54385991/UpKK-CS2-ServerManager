"use client";

import { toast } from "sonner";

/** App-wide toast API. Use for action results, not form-field validation. */
export const notify = {
  success(message: string) {
    toast.success(message);
  },
  error(message: string) {
    toast.error(message, { duration: 6000 });
  },
  warning(message: string) {
    toast.warning(message);
  },
  info(message: string) {
    toast.info(message);
  },
  message(message: string) {
    toast(message);
  },
  dismiss(id?: string | number) {
    toast.dismiss(id);
  },
};
