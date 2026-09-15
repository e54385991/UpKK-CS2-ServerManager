"use client";

import { memo } from "react";
import { useTranslations } from "next-intl";
import type { AssistantConversationDetail } from "@/modules/assistant/types";

export const AssistantMessages = memo(function AssistantMessages({
  messages,
}: {
  messages: AssistantConversationDetail["messages"];
}) {
  const t = useTranslations("assistant");
  return (
    <>
      {messages.map((message) => (
        <div key={message.id} className="space-y-1">
          <p className="text-xs font-medium text-fg-subtle">
            {message.role === "user"
              ? t("roleUser")
              : message.role === "assistant"
                ? t("roleAssistant")
                : message.role}
          </p>
          <p className="whitespace-pre-wrap text-sm text-fg">
            {message.content || message.toolName || "—"}
          </p>
        </div>
      ))}
    </>
  );
});
