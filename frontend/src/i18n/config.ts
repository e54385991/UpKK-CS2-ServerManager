export const LOCALES = ["en-US", "zh-CN"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en-US";

/** Non-HttpOnly cookie so the language switcher can set it client-side. */
export const LOCALE_COOKIE = "locale";

export const LOCALE_LABELS: Record<Locale, string> = {
  "zh-CN": "简体中文",
  "en-US": "English",
};

export function isLocale(value: string | undefined | null): value is Locale {
  return value != null && (LOCALES as readonly string[]).includes(value);
}

/**
 * Map the browser's highest-priority accepted language to the two locales the
 * console supports. Every Chinese language tag uses the Simplified Chinese
 * catalog; all other or malformed values fall back to English.
 */
export function localeFromAcceptLanguage(value: string | undefined | null): Locale {
  if (!value) return DEFAULT_LOCALE;

  const preferred = value
    .split(",")
    .map((part, index) => {
      const [rawTag = "", ...parameters] = part.trim().split(";");
      const tag = rawTag.trim().toLowerCase();
      const qualityParameter = parameters.find((parameter) =>
        parameter.trim().toLowerCase().startsWith("q="),
      );
      const quality = qualityParameter
        ? Number(qualityParameter.trim().slice(2))
        : 1;
      return {
        tag,
        quality: Number.isFinite(quality) && quality >= 0 && quality <= 1 ? quality : 0,
        index,
      };
    })
    .filter(({ tag, quality }) => tag.length > 0 && quality > 0)
    .sort((left, right) => right.quality - left.quality || left.index - right.index)[0]?.tag;

  return preferred === "zh" || preferred?.startsWith("zh-")
    ? "zh-CN"
    : DEFAULT_LOCALE;
}

export function resolveLocale(
  cookieLocale: string | undefined | null,
  acceptLanguage: string | undefined | null,
): Locale {
  return isLocale(cookieLocale)
    ? cookieLocale
    : localeFromAcceptLanguage(acceptLanguage);
}
