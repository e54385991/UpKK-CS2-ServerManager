import { proxyPasskeys } from "@/modules/profile/passkey-proxy";


export function POST(request: Request) {
  return proxyPasskeys(request);
}

export function PATCH(request: Request) {
  return proxyPasskeys(request);
}

export function DELETE(request: Request) {
  return proxyPasskeys(request);
}
