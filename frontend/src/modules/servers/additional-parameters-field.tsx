"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

export const WORKSHOP_STARTUP_EXAMPLE =
  "+sv_hibernate_when_empty 0 +host_workshop_map 3171881962 -nohltv";

const OFFICIAL_MAPS = [
  "de_dust2",
  "de_mirage",
  "de_inferno",
  "de_nuke",
  "de_ancient",
  "de_anubis",
  "de_vertigo",
] as const;

const EXAMPLES = [
  { id: "ze", value: WORKSHOP_STARTUP_EXAMPLE },
  { id: "kz", value: WORKSHOP_STARTUP_EXAMPLE },
] as const;

export function OfficialMapField({
  id,
  name,
  defaultValue,
  required = true,
  className,
}: {
  id: string;
  name: string;
  defaultValue?: string;
  required?: boolean;
  className?: string;
}) {
  const t = useTranslations("serverConfig");
  const listId = `${id}-options`;
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>{t("fields.defaultMap")}</Label>
      <Input
        id={id}
        name={name}
        required={required}
        defaultValue={defaultValue}
        list={listId}
      />
      <datalist id={listId}>
        {OFFICIAL_MAPS.map((map) => (
          <option key={map} value={map} />
        ))}
      </datalist>
      <p className="text-xs text-fg-subtle">{t("workshopMapHint")}</p>
    </div>
  );
}

export function AdditionalParametersField({
  id,
  name,
  value,
  className,
  onChange,
}: {
  id: string;
  name: string;
  value: string;
  className?: string;
  onChange: (value: string) => void;
}) {
  const t = useTranslations("serverConfig");
  return (
    <div className={cn("space-y-3", className)} data-testid="additional-parameters">
      <div className="space-y-1.5">
        <Label htmlFor={id}>{t("fields.additionalParameters")}</Label>
        <Textarea
          id={id}
          name={name}
          rows={3}
          value={value}
          data-testid="additional-parameters-input"
          className="font-mono text-sm"
          placeholder={t("additionalParametersPlaceholder")}
          onChange={(event) => onChange(event.target.value)}
        />
        <p className="text-xs text-fg-subtle">{t("additionalParametersHint")}</p>
      </div>
      <div className="space-y-2">
        <p className="text-sm font-medium text-fg">{t("additionalParametersExamples")}</p>
        {EXAMPLES.map((example) => (
          <div
            key={example.id}
            className="space-y-2 rounded-md border border-line bg-surface-overlay/50 px-3 py-2"
          >
            <div>
              <p className="text-sm font-medium text-fg">
                {t(`additionalParametersExample.${example.id}.title`)}
              </p>
              <p className="text-xs text-fg-subtle">
                {t(`additionalParametersExample.${example.id}.help`)}
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <code className="min-w-0 flex-1 break-all font-mono text-xs text-fg-muted">
                {example.value}
              </code>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid={`additional-parameters-use-${example.id}`}
                onClick={() => onChange(example.value)}
              >
                {t("additionalParametersUse")}
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
