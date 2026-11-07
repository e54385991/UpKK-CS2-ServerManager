"use client";

import { startTransition, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { saveGameCommandsAction } from "./game-command-actions";
import type { GameArgumentSetting, PersonalGameCommand } from "./game-command-types";
import { Button } from "@/shared/ui/button";
import { Card, CardContent } from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function GameCommandForm({ initial }: { initial: PersonalGameCommand[] | null }) {
  const t = useTranslations("profile.gameCommands");
  const [commands, setCommands] = useState(initial ?? []);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  function update(index: number, patch: Partial<PersonalGameCommand>) {
    setCommands((current) => current.map((item, row) => row === index ? { ...item, ...patch } : item));
    setBanner(null);
  }
  function argument(index: number, position: number, patch: Partial<GameArgumentSetting>) {
    const command = commands[index];
    if (command) update(index, { arguments: command.arguments.map((arg, row) => row === position ? { ...arg, ...patch } : arg) });
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || initial === null) return;
    setSaving(true);
    setBanner(null);
    startTransition(async () => {
      try {
        const result = await saveGameCommandsAction(commands);
        if (result.ok) {
          setCommands(result.data);
          setBanner(t("saved"));
        } else setBanner(result.error || t("failed"));
      } catch { setBanner(t("failed")); }
      finally { setSaving(false); }
    });
  }
  if (initial === null) return <Card className="p-5 text-warn">{t("loadError")}</Card>;

  return <form onSubmit={submit} className="space-y-4" data-testid="game-command-form">
    <p className="text-sm text-fg-muted">{t("help")}</p>
    <fieldset disabled={saving} className="min-w-0 space-y-4">
      {commands.map((item, index) => {
        const builtIn = item.name === "css_wsmap" || item.name === "css_ext_maptime";
        const key = `command-${index}`;
        return <Card key={index}><CardContent className="space-y-4 pt-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor={`${key}-name`}>{t("name")}</Label><Input id={`${key}-name`} required pattern="[A-Za-z][A-Za-z0-9_]{0,63}" maxLength={64} value={item.name} onChange={(event) => update(index, { name: event.target.value })} /></div>
            <div><Label htmlFor={`${key}-description`}>{t("description")}</Label><Input id={`${key}-description`} required maxLength={500} value={item.description} onChange={(event) => update(index, { description: event.target.value })} /></div>
          </div>
          <div className="flex flex-wrap gap-4 text-sm text-fg-muted">
            <label className="flex items-center gap-2"><input type="checkbox" checked={item.enabled} onChange={(event) => update(index, { enabled: event.target.checked })} />{t("enabled")}</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={item.requiresEmpty} disabled={item.name === "css_wsmap"} onChange={(event) => update(index, { requiresEmpty: event.target.checked })} />{t("empty")}</label>
            <span>{t("online")}</span>
          </div>
          {builtIn ? <p className="text-xs text-fg-subtle">{t("builtIn")}</p> : null}
          {item.arguments.map((arg, position) => {
            const id = `${key}-arg-${position}`;
            return <fieldset key={position} disabled={builtIn} className="grid min-w-0 gap-3 rounded-md border border-line p-3 sm:grid-cols-2">
              <legend className="px-1 text-sm">{t("argument", { number: position + 1 })}</legend>
              <div><Label htmlFor={`${id}-name`}>{t("name")}</Label><Input id={`${id}-name`} required pattern="[A-Za-z][A-Za-z0-9_]{0,63}" maxLength={64} value={arg.name} onChange={(event) => argument(index, position, { name: event.target.value })} /></div>
              <div><Label htmlFor={`${id}-description`}>{t("description")}</Label><Input id={`${id}-description`} required maxLength={300} value={arg.description} onChange={(event) => argument(index, position, { description: event.target.value })} /></div>
              <div><Label htmlFor={`${id}-kind`}>{t("kind")}</Label><Select id={`${id}-kind`} value={arg.kind} onChange={(event) => argument(index, position, { kind: event.target.value as GameArgumentSetting["kind"] })}>
                <option value="token">{t("token")}</option><option value="integer">{t("integer")}</option><option value="choice">{t("choice")}</option>
              </Select></div>
              <div><Label htmlFor={`${id}-length`}>{t("length")}</Label><Input id={`${id}-length`} type="number" min={1} max={128} required value={arg.maxLength} onChange={(event) => argument(index, position, { maxLength: Number(event.target.value) })} /></div>
              {arg.kind === "integer" ? <>
                <div><Label htmlFor={`${id}-min`}>{t("minimum")}</Label><Input id={`${id}-min`} type="number" min={0} max={999999999} required value={arg.minimum} onChange={(event) => argument(index, position, { minimum: Number(event.target.value) })} /></div>
                <div><Label htmlFor={`${id}-max`}>{t("maximum")}</Label><Input id={`${id}-max`} type="number" min={0} max={999999999} required value={arg.maximum} onChange={(event) => argument(index, position, { maximum: Number(event.target.value) })} /></div>
              </> : null}
              {arg.kind === "choice" ? <div className="sm:col-span-2"><Label htmlFor={`${id}-choices`}>{t("choices")}</Label><Input id={`${id}-choices`} required value={arg.choices.join(",")} onChange={(event) => argument(index, position, { choices: event.target.value.split(",") })} /></div> : null}
              <Button type="button" variant="secondary" onClick={() => update(index, { arguments: item.arguments.filter((_, row) => row !== position) })}>{t("removeArgument")}</Button>
            </fieldset>;
          })}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" disabled={builtIn || item.arguments.length >= 16} onClick={() => update(index, { arguments: [...item.arguments, { name: "value", description: "ASCII token.", kind: "token", maxLength: 128, minimum: 1, maximum: 86400, choices: [] }] })}>{t("addArgument")}</Button>
            <Button type="button" variant="danger" onClick={() => setCommands(commands.filter((_, row) => row !== index))}>{t("remove")}</Button>
          </div>
        </CardContent></Card>;
      })}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" disabled={commands.length >= 100} onClick={() => setCommands([...commands, { name: "", description: "", arguments: [], enabled: true, requiresEmpty: true, requiresOnline: true }])}>{t("add")}</Button>
        <Button type="submit">{saving ? t("saving") : t("save")}</Button>
      </div>
    </fieldset>
    {banner ? <p role="status" className="text-sm text-fg-muted">{banner}</p> : null}
  </form>;
}
