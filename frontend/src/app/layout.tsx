import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import { Suspense } from "react";
import { publicAppUrlFromHeaders } from "@/shared/config/public-app-url";
import { FeedbackHost } from "@/shared/feedback/feedback-host";
import { RuntimeFooter, RuntimeFooterSkeleton } from "@/modules/shell/runtime-footer";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("site");
  return {
    metadataBase: new URL(publicAppUrlFromHeaders(await headers())),
    title: {
      default: t("name"),
      template: `%s · ${t("name")}`,
    },
    description: t("tagline"),
    applicationName: t("name"),
    robots: { index: false, follow: false },
  };
}

export const viewport: Viewport = {
  themeColor: "#08090c",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale} suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <FeedbackHost />
          <div className="flex h-dvh flex-col overflow-hidden">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              {children}
            </div>
            <Suspense fallback={<RuntimeFooterSkeleton />}>
              <RuntimeFooter />
            </Suspense>
          </div>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
