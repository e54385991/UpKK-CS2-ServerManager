"use client";

import { useMemo } from "react";
import CodeMirror, { EditorView } from "@uiw/react-codemirror";
import { loadLanguage, langNames, type LanguageName } from "@uiw/codemirror-extensions-langs";
import { vscodeDark } from "@uiw/codemirror-theme-vscode";
import { editorLanguageId } from "@/modules/files/language";

const fillParent = EditorView.theme({
  "&": { height: "100%" },
  "& .cm-scroller": { overflow: "auto" },
});

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
    const language =
      languageId && (langNames as readonly string[]).includes(languageId)
        ? loadLanguage(languageId as LanguageName)
        : null;
    return language ? [fillParent, language] : [fillParent];
  }, [fileName]);

  return (
    <div className="file-code-editor flex h-full min-h-0 flex-col overflow-hidden [&_.cm-theme]:h-full [&_.cm-theme]:min-h-0 [&_.cm-theme]:overflow-hidden [&_.cm-editor]:h-full [&_.cm-editor]:overflow-hidden [&_.cm-scroller]:overflow-auto [&_.cm-scroller]:font-mono [&_.cm-scroller]:text-[13px]">
      <CodeMirror
        value={value}
        height="100%"
        maxHeight="100%"
        className="min-h-0 flex-1 overflow-hidden"
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
