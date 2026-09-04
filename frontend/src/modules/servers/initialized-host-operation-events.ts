export function initializedHostOperationEventsUrl(
  initializedServerId: number,
  operationId: string,
  after = "0",
): string {
  return `/ops-stream/initialized-servers/${initializedServerId}/operations/${operationId}?after=${encodeURIComponent(after)}`;
}
