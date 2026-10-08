"use client";
import { useTranslations } from "next-intl";
import { NAV_SECTIONS } from "@/shared/config/navigation";
import { NavLink } from "./nav-link";

export function AdminNavigation() {
  const t = useTranslations("nav");
  const items = NAV_SECTIONS.flatMap(section => section.items).filter(item => item.adminOnly);
  return items.map(item => <NavLink key={item.href} href={item.href} label={t(item.key)}
    icon={item.icon} navKey={item.key} items={items} />);
}
