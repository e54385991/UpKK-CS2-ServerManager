export const PROFILE_SECTIONS = [
  { id: "profile-account", key: "account" },
  { id: "profile-credentials", key: "credentials" },
  { id: "profile-security", key: "security" },
  { id: "profile-backups", key: "backups" },
  { id: "profile-ai", key: "ai" },
  { id: "profile-operations", key: "operations" },
] as const;

export type ProfileSectionKey = (typeof PROFILE_SECTIONS)[number]["key"];

const HASH_ALIASES: Record<string, ProfileSectionKey> = {
  "profile-github-token": "credentials",
  "profile-steam-key": "credentials",
};

export function profileSectionFromHash(hash: string): ProfileSectionKey {
  const id = hash.startsWith("#") ? hash.slice(1) : hash;
  return HASH_ALIASES[id] ?? PROFILE_SECTIONS.find((section) => section.id === id)?.key ?? "account";
}
