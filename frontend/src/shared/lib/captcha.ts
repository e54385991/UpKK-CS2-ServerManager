export type CaptchaChallenge = {
  token: string;
  imageUrl: string;
  enabled: boolean;
};

type ChallengeJson = { token?: unknown; image?: unknown; enabled?: unknown };

/**
 * Load a CAPTCHA without relying on `X-Captcha-Token` (proxies and some
 * browsers drop custom headers). Prefers the JSON challenge; falls back to
 * the image endpoint used by the legacy UI.
 */
export async function fetchCaptchaChallenge(): Promise<CaptchaChallenge | null> {
  const fromJson = await fetchJsonChallenge();
  if (fromJson) return fromJson;
  return fetchImageChallenge();
}

async function fetchJsonChallenge(): Promise<CaptchaChallenge | null> {
  try {
    const response = await fetch(`/api/captcha/challenge?ts=${Date.now()}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    const body = (await response.json()) as ChallengeJson;
    if (body.enabled === false) {
      return { token: "", imageUrl: "", enabled: false };
    }
    if (typeof body.token !== "string" || !body.token) return null;
    if (typeof body.image !== "string" || !body.image.startsWith("data:image/")) {
      return null;
    }
    return { token: body.token, imageUrl: body.image, enabled: true };
  } catch {
    return null;
  }
}

async function fetchImageChallenge(): Promise<CaptchaChallenge | null> {
  try {
    const response = await fetch(`/api/captcha/image/refresh?ts=${Date.now()}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    const token = response.headers.get("X-Captcha-Token");
    if (!token) return null;
    const blob = await response.blob();
    if (!blob.size) return null;
    return { token, imageUrl: URL.createObjectURL(blob), enabled: true };
  } catch {
    return null;
  }
}
