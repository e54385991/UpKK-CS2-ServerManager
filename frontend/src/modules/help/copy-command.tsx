"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, Clipboard } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { copyText } from "@/shared/lib/clipboard";

export function CopyCommand({ text }: { text: string }) {
  const t = useTranslations("serverHelp");
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  async function onCopy() {
    try {
      const ok = await copyText(text);
      setCopied(ok);
      setFailed(!ok);
      if (!ok && preRef.current) {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(preRef.current);
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
      window.setTimeout(() => {
        setCopied(false);
        setFailed(false);
      }, 2200);
    } catch {
      setCopied(false);
      setFailed(true);
    }
  }

  return (
    <div className="relative rounded-md border border-line bg-surface-raised px-3 py-2.5">
      <pre ref={preRef} className="overflow-x-auto pr-20 font-mono text-xs text-fg whitespace-pre-wrap">
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
        {copied ? t("copied") : failed ? t("copyFailed") : t("copy")}
      </Button>
    </div>
  );
}
