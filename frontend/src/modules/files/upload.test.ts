import assert from "node:assert/strict";
import test from "node:test";
import {
  formatTransferRate,
  isSafeUploadRelativePath,
  uploadBasename,
  uploadErrorMessage,
  uploadRelativePath,
  uploadsFromFileList,
} from "./upload.ts";

test("uploadRelativePath strips parent segments and uses webkitRelativePath", () => {
  const nested = { name: "server.cfg", webkitRelativePath: "cfg/../cfg/server.cfg" } as File;
  assert.equal(uploadRelativePath(nested), "cfg/cfg/server.cfg");
  const plain = { name: "motd.txt", webkitRelativePath: "" } as File;
  assert.equal(uploadRelativePath(plain), "motd.txt");
});

test("uploadBasename and isSafeUploadRelativePath cover folder names", () => {
  assert.equal(uploadBasename("plugin/cfg/server.cfg"), "server.cfg");
  assert.equal(isSafeUploadRelativePath("plugin/cfg/server.cfg"), true);
  assert.equal(isSafeUploadRelativePath("Folder/Icon\r"), false);
  assert.equal(isSafeUploadRelativePath(""), false);
});

test("uploadsFromFileList drops control-character paths", () => {
  const bad = { name: "Icon\r", webkitRelativePath: "Folder/Icon\r" } as File;
  const good = { name: "a.cfg", webkitRelativePath: "Folder/a.cfg" } as File;
  assert.deepEqual(
    uploadsFromFileList([bad, good]).map((item) => item.relativePath),
    ["Folder/a.cfg"],
  );
});

test("formatTransferRate and uploadErrorMessage", () => {
  assert.equal(formatTransferRate(0), "—");
  assert.equal(formatTransferRate(1536), "1.5 KB/s");
  assert.equal(
    uploadErrorMessage(JSON.stringify({ detail: "relative_path must stay inside the destination folder" }), "fail"),
    "relative_path must stay inside the destination folder",
  );
  assert.equal(uploadErrorMessage("", "fail"), "fail");
});
