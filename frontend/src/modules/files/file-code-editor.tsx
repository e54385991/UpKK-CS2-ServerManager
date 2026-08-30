"use client";

import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { loadLanguage, langNames, type LanguageName } from "@uiw/codemirror-extensions-langs";
import { vscodeDark } from "@uiw/codemirror-theme-vscode";
import { editorLanguageId } from "@/modules/files/language";

export function FileCodeEditor({
  value,
  fileName,
  readOnly,
  onChange,
}: {
  value: string;
  fileName: string;
  readOnly?: boolean;
  onChange: (value: string) => void;
}) {
  const extensions = useMemo(() => {
    const languageId = editorLanguageId(fileName);
    if (!languageId || !(langNames as readonly string[]).includes(languageId)) {
      return [];
    }
    const language = loadLanguage(languageId as LanguageName);
    return language ? [language] : [];
  }, [fileName]);

  return (
    <div className="file-code-editor h-full min-h-0 [&_.cm-editor]:h-full [&_.cm-scroller]:font-mono [&_.cm-scroller]:text-[13px]">
      <CodeMirror
        value={value}
        height="100%"
        theme={vscodeDark}
        editable={!readOnly}
        readOnly={readOnly}
        autoFocus={!readOnly}
        basicSetup={{
          foldGutter: true,
          dropCursor: true,
          allowMultipleSelections: true,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: true,
          highlightActiveLine: true,
          highlightSelectionMatches: true,
          searchKeymap: true,
        }}
        extensions={extensions}
        onChange={onChange}
      />
    </div>
  );
}
