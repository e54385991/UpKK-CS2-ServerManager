const PASSKEY_ERROR_CODES = [
  "passkey_insecure_origin",
  "passkey_ip_origin",
  "passkey_missing_origin",
  "passkey_origin_mismatch",
  "passkey_rp_mismatch",
  "passkey_challenge_expired",
  "passkey_verification_failed",
  "passkey_cloned",
  "passkey_limit_reached",
  "passkey_not_found",
  "passkey_duplicate",
  "passkey_challenge_store_failed",
] as const;

export type PasskeyErrorCode = (typeof PASSKEY_ERROR_CODES)[number];

const PASSKEY_ERROR_CODE_SET = new Set<string>(PASSKEY_ERROR_CODES);

export function isPasskeyErrorCode(value: string): value is PasskeyErrorCode {
  return PASSKEY_ERROR_CODE_SET.has(value);
}

export function bufferToBase64url(buffer: BufferSource): string {
  const bytes =
    buffer instanceof ArrayBuffer
      ? new Uint8Array(buffer)
      : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
}

export function base64urlToBuffer(value: string): ArrayBuffer {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "===".slice((value.length + 3) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

export function isPasskeySupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential === "function" &&
    typeof navigator.credentials?.get === "function" &&
    typeof navigator.credentials.create === "function"
  );
}

export function passkeyEnvironmentIssue(): "ip" | "insecure" | null {
  if (typeof window === "undefined") return "insecure";
  const hostname = window.location.hostname.toLowerCase();
  if (hostnameIsIp(hostname)) return "ip";
  if (!window.isSecureContext && hostname !== "localhost" && hostname !== "localhost.localdomain") {
    return "insecure";
  }
  return null;
}

export function hostnameIsIp(hostname: string): boolean {
  const host = hostname.trim().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host === "localhost.localdomain") return false;
  return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(host) || host.includes(":");
}

type JsonRecord = Record<string, unknown>;

type PublicKeyCredentialJSONMethods = typeof PublicKeyCredential & {
  parseCreationOptionsFromJSON?: (
    options: JsonRecord,
  ) => PublicKeyCredentialCreationOptions;
  parseRequestOptionsFromJSON?: (
    options: JsonRecord,
  ) => PublicKeyCredentialRequestOptions;
};

function descriptorFromJson(raw: JsonRecord): PublicKeyCredentialDescriptor {
  const transports = Array.isArray(raw.transports)
    ? (raw.transports.filter((item) => typeof item === "string") as AuthenticatorTransport[])
    : undefined;
  return {
    type: "public-key",
    id: base64urlToBuffer(String(raw.id)),
    ...(transports && transports.length > 0 ? { transports } : {}),
  };
}

export function creationOptionsFromJson(
  publicKey: JsonRecord,
): PublicKeyCredentialCreationOptions {
  const parse = (PublicKeyCredential as PublicKeyCredentialJSONMethods)
    .parseCreationOptionsFromJSON;
  if (typeof parse === "function") return parse(publicKey);
  const user = (publicKey.user ?? {}) as JsonRecord;
  const exclude = Array.isArray(publicKey.excludeCredentials)
    ? (publicKey.excludeCredentials as JsonRecord[]).map(descriptorFromJson)
    : [];
  return {
    ...(publicKey as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64urlToBuffer(String(publicKey.challenge)),
    user: {
      id: base64urlToBuffer(String(user.id)),
      name: String(user.name ?? ""),
      displayName: String(user.displayName ?? user.name ?? ""),
    },
    excludeCredentials: exclude,
  };
}

export function requestOptionsFromJson(
  publicKey: JsonRecord,
): PublicKeyCredentialRequestOptions {
  const parse = (PublicKeyCredential as PublicKeyCredentialJSONMethods)
    .parseRequestOptionsFromJSON;
  if (typeof parse === "function") return parse(publicKey);
  const allow = Array.isArray(publicKey.allowCredentials)
    ? (publicKey.allowCredentials as JsonRecord[]).map(descriptorFromJson)
    : [];
  return {
    ...(publicKey as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64urlToBuffer(String(publicKey.challenge)),
    allowCredentials: allow.length > 0 ? allow : undefined,
  };
}

export function credentialToJson(credential: PublicKeyCredential): JsonRecord {
  const toJSON = (credential as { toJSON?: () => unknown }).toJSON;
  if (typeof toJSON === "function") return toJSON.call(credential) as JsonRecord;
  const extensions =
    typeof credential.getClientExtensionResults === "function"
      ? credential.getClientExtensionResults()
      : {};
  if (credential.response instanceof AuthenticatorAttestationResponse) {
    const transports =
      typeof credential.response.getTransports === "function"
        ? credential.response.getTransports()
        : [];
    return {
      id: credential.id,
      rawId: bufferToBase64url(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
      response: {
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        attestationObject: bufferToBase64url(credential.response.attestationObject),
        transports,
      },
      clientExtensionResults: extensions,
    };
  }
  const assertion = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    response: {
      clientDataJSON: bufferToBase64url(assertion.clientDataJSON),
      authenticatorData: bufferToBase64url(assertion.authenticatorData),
      signature: bufferToBase64url(assertion.signature),
      userHandle: assertion.userHandle ? bufferToBase64url(assertion.userHandle) : null,
    },
    clientExtensionResults: extensions,
  };
}

export async function extractPasskeyDetail(response: Response): Promise<string | null> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    return typeof data.detail === "string" ? data.detail : null;
  } catch {
    return null;
  }
}
