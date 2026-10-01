"use client";


export type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export type SettingsSectionKey =
  | "downloads"
  | "notifications"
  | "security"
  | "logging"
  | "hidden";

