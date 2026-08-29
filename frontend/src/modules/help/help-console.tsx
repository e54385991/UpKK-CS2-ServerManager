"use client";

import { useState } from "react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { Check, Clipboard, ExternalLink } from "lucide-react";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

const DOCS_URL = "https://github.com/e54385991/UpKK-CS2-ServerManager";
const LIBSSL_DEB = "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb";
const LIBSSL_URL = `https://security.ubuntu.com/ubuntu/pool/main/o/openssl/${LIBSSL_DEB}`;

function CopyCommand({ text }: { text: string }) {
  const t = useTranslations("serverHelp");
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="relative rounded-md border border-line bg-surface-raised px-3 py-2.5">
      <pre className="overflow-x-auto pr-20 font-mono text-xs text-fg whitespace-pre-wrap">
        {text}
      </pre>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="absolute top-1.5 right-1.5"
        onClick={() => void onCopy()}
      >
        {copied ? <Check /> : <Clipboard />}
        {copied ? t("copied") : t("copy")}
      </Button>
    </div>
  );
}

export function HelpConsole({
  host,
  gamePort,
}: {
  host: string;
  gamePort: number;
}) {
  const t = useTranslations("serverHelp");
  const port = String(gamePort);

  return (
    <div className="max-w-3xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
      </Card>

      <Card className="border-warn/30">
        <CardHeader>
          <CardTitle>{t("kzTitle")}</CardTitle>
          <CardDescription>{t("kzIssue")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-md border border-line bg-surface-raised px-4 py-3 text-sm">
            <p className="font-medium text-fg">{t("rootRequired")}</p>
            <p className="mt-1 text-fg-muted">{t("highPrivilege")}</p>
          </div>
          <ol className="list-decimal space-y-3 pl-5 text-sm">
            <li>
              <p className="mb-2 font-medium text-fg">{t("kzStep1")}</p>
              <CopyCommand text={`wget ${LIBSSL_URL}`} />
            </li>
            <li>
              <p className="mb-2 font-medium text-fg">{t("kzStep2")}</p>
              <CopyCommand text={`sudo dpkg -i ${LIBSSL_DEB}`} />
            </li>
            <li>
              <p className="font-medium text-fg">{t("kzStep3")}</p>
              <p className="mt-1 text-fg-muted">{t("kzStep3Help")}</p>
            </li>
          </ol>
          <p className="text-sm text-warn">{t("manualOnly")}</p>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:text-primary-strong"
          >
            <ExternalLink className="size-3.5" />
            {t("viewDocs")}
          </a>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("joinTitle")}</CardTitle>
          <CardDescription>{t("joinHelp")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-fg">{t("cloudTitle")}</h3>
            <p className="text-sm text-fg-muted">
              {t("securityGroup", { port })}
            </p>
            <p className="text-xs text-fg-subtle">{t("aliyunExample")}</p>
            <Image
              src="/static/images/aliyun-deploy/firewall/firewall-ali.webp"
              alt={t("firewallAlt")}
              width={1280}
              height={720}
              unoptimized
              className="h-auto w-full rounded-md border border-line"
            />
            <p className="text-xs text-fg-subtle">{t("otherClouds")}</p>
            <p className="text-sm text-fg-muted">{t("firewallHint", { port })}</p>
            <CopyCommand text={`ufw allow ${port}/udp`} />
            <p className="text-sm text-fg-muted">{t("listenHint", { port })}</p>
            <CopyCommand text={`netstat -tuln | grep ${port}`} />
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-fg">{t("homeTitle")}</h3>
            <p className="text-sm text-fg-muted">{t("publicIp")}</p>
            <p className="text-sm text-fg-muted">{t("portForward", { port })}</p>
            <p className="text-sm text-warn">{t("noPublicIp")}</p>
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-fg">{t("udpTitle")}</h3>
            <p className="text-sm text-fg-muted">{t("udpHint")}</p>
            <CopyCommand text={`nc -vzu ${host} ${port}`} />
            <p className="text-xs text-fg-subtle">{t("ncNote")}</p>
          </section>

          <p className="text-xs text-fg-subtle">{t("portNote", { port })}</p>
        </CardContent>
      </Card>
    </div>
  );
}
