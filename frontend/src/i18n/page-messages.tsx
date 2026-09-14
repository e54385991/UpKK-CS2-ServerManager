import { ClientMessages } from "@/i18n/client-messages";
import {
  SERVERS_SECTION_NAMESPACES,
  withConsoleNamespaces,
} from "@/i18n/namespaces";
import type { MessageNamespace } from "@/i18n/pick-messages";

export function ServersSectionMessages({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClientMessages namespaces={SERVERS_SECTION_NAMESPACES}>
      {children}
    </ClientMessages>
  );
}

export function DomainMessages({
  extra,
  children,
}: {
  extra: readonly MessageNamespace[];
  children: React.ReactNode;
}) {
  return (
    <ClientMessages namespaces={withConsoleNamespaces(...extra)}>
      {children}
    </ClientMessages>
  );
}
