import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

for (const [deploymentId, gitSha, expectedId] of [
  ["fixture-deployment", "fixture-commit", "fixture-deployment"],
  ["unknown", "fixture-commit", "fixture-commit"],
  ["", "fixture-commit", "fixture-commit"],
  ["unknown", "unknown", ""],
]) {
  test(`Docker compiler receives the build secret and deployment ID ${deploymentId || "(empty)"}/${gitSha}`, () => {
    const source = readFileSync("frontend/Dockerfile", "utf8").split("\n");
    const start = source.findIndex((line) => line.startsWith("RUN --mount=type=secret,id=next_server_actions_key"));
    assert.notEqual(start, -1);
    const command = [];
    for (const line of source.slice(start + 1)) {
      command.push(line.replace(/\\$/, ""));
      if (!line.endsWith("\\")) break;
    }

    const directory = mkdtempSync(join(tmpdir(), "upkk-next-build-"));
    try {
      const binaryDirectory = join(directory, "bin");
      mkdirSync(binaryDirectory);
      const secretPath = join(directory, "actions-key");
      const key = Buffer.alloc(32, 1).toString("base64");
      writeFileSync(secretPath, key);
      const compiler = join(binaryDirectory, "npx");
      writeFileSync(compiler, `#!/bin/sh
  printf '%s\\n' "$*" "$NEXT_DEPLOYMENT_ID" "$NEXT_SERVER_ACTIONS_ENCRYPTION_KEY"
  `);
      chmodSync(compiler, 0o755);
      const quotedSecretPath = `'${secretPath.replaceAll("'", "'\"'\"'")}'`;
      const output = execFileSync("sh", ["-c", command.join(" ").replace("/run/secrets/next_server_actions_key", quotedSecretPath)], {
        cwd: directory,
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${binaryDirectory}:${process.env.PATH}`,
          DEPLOYMENT_ID: deploymentId,
          GIT_SHA: gitSha,
          NEXT_DEPLOYMENT_ID: "incorrect-runtime-id",
          NEXT_SERVER_ACTIONS_ENCRYPTION_KEY: "incorrect-runtime-key",
        },
      });
      assert.deepEqual(output.trim().split("\n"), ["next build", expectedId, key]);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
}
