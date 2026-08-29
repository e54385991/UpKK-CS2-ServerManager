import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import { FeedbackHost } from "@/shared/feedback/feedback-host";
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
    title: {
      default: `${t("name")} · ${t("fullName")}`,
      template: `%s · ${t("name")}`,
    },
    description: t("tagline"),
    applicationName: t("fullName"),
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
          {children}
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
