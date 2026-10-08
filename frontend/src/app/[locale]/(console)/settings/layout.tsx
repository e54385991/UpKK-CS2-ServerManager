import { ClientMessages } from "@/i18n/client-messages";
import { SETTINGS_TREE_NAMESPACES } from "@/i18n/namespaces";

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClientMessages namespaces={SETTINGS_TREE_NAMESPACES}>
      {children}
    </ClientMessages>
  );
}
