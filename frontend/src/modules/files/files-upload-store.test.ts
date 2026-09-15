import assert from "node:assert/strict";
import test from "node:test";
import {
  getFilesUploadSnapshot,
  resetFilesUpload,
  setFilesUploadItems,
  setFilesUploadRate,
} from "./files-upload-store.ts";
import { filterAndSortEntries, type FileEntry } from "./types.ts";

function entry(name: string): FileEntry {
  return {
    name,
    path: `/game/${name}`,
    type: "file",
    size: 1,
    modified: 0,
    permissions: "644",
    isSymlink: false,
  };
}

test("upload progress updates do not rebuild a directory listing", () => {
  resetFilesUpload();
  const files = [entry("b.cfg"), entry("a.cfg")];
  const listed = filterAndSortEntries(files, "", "all", "name", "asc");
  setFilesUploadItems([
    {
      id: "1",
      name: "pack.zip",
      relativePath: "pack.zip",
      size: 10,
      loaded: 4,
      status: "uploading",
    },
  ]);
  setFilesUploadRate(12);
  const afterProgress = filterAndSortEntries(files, "", "all", "name", "asc");
  assert.deepEqual(
    afterProgress.map((item) => item.name),
    listed.map((item) => item.name),
  );
  assert.equal(getFilesUploadSnapshot().rate, 12);
  assert.equal(getFilesUploadSnapshot().items[0]?.loaded, 4);
  resetFilesUpload();
  assert.deepEqual(getFilesUploadSnapshot(), { items: [], rate: 0 });
});
