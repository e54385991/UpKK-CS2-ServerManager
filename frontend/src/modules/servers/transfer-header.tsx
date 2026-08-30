import { listServers } from "@/modules/servers/api";
import { ServerTransferButton } from "@/modules/servers/transfer-button";

export async function ServerTransferHeader() {
  const result = await listServers();
  const servers = result.ok
    ? result.data.map((server) => ({
        id: server.id,
        name: server.name,
        host: server.host,
        gamePort: server.gamePort,
      }))
    : [];
  return <ServerTransferButton servers={servers} />;
}
