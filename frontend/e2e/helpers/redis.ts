import { createConnection } from "node:net";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Test-process Redis GET for CAPTCHA codes stored as `captcha:<token>`.
 * Production login still requires a real CAPTCHA; this only reads the same
 * key FastAPI already writes. Never expose this over HTTP.
 */

type RedisSettings = {
  host: string;
  port: number;
  password: string;
  db: number;
};

function parseEnvFile(filePath: string): Record<string, string> {
  if (!existsSync(filePath)) return {};
  const values: Record<string, string> = {};
  for (const raw of readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function firstDefined(
  ...candidates: Array<string | undefined>
): string | undefined {
  return candidates.find((value) => value != null && value !== "");
}

function loadRedisSettings(): RedisSettings {
  const repoEnv = parseEnvFile(resolve(process.cwd(), "..", ".env"));
  const frontendEnv = parseEnvFile(resolve(process.cwd(), ".env"));
  return {
    host:
      firstDefined(process.env.REDIS_HOST, frontendEnv.REDIS_HOST, repoEnv.REDIS_HOST) ??
      "127.0.0.1",
    port: Number(
      firstDefined(process.env.REDIS_PORT, frontendEnv.REDIS_PORT, repoEnv.REDIS_PORT) ??
        "6379",
    ),
    password:
      firstDefined(
        process.env.REDIS_PASSWORD,
        frontendEnv.REDIS_PASSWORD,
        repoEnv.REDIS_PASSWORD,
      ) ?? "",
    db: Number(
      firstDefined(process.env.REDIS_DB, frontendEnv.REDIS_DB, repoEnv.REDIS_DB) ??
        "0",
    ),
  };
}

function encodeCommand(args: readonly string[]): Buffer {
  const chunks = [`*${args.length}\r\n`];
  for (const arg of args) {
    const payload = Buffer.from(arg, "utf8");
    chunks.push(`$${payload.length}\r\n`, arg, "\r\n");
  }
  return Buffer.from(chunks.join(""));
}

type RespValue = string | null;

function tryParseReply(
  buffer: Buffer,
): { value: RespValue; rest: Buffer } | null {
  if (buffer.length < 3) return null;
  const type = String.fromCharCode(buffer[0] ?? 0);
  const headerEnd = buffer.indexOf("\r\n");
  if (headerEnd < 0) return null;
  const header = buffer.subarray(1, headerEnd).toString("utf8");
  if (type === "-") {
    throw new Error(`Redis error: ${header}`);
  }
  if (type === "+" || type === ":") {
    return { value: header, rest: buffer.subarray(headerEnd + 2) };
  }
  if (type === "$") {
    const size = Number(header);
    if (size < 0) {
      return { value: null, rest: buffer.subarray(headerEnd + 2) };
    }
    const start = headerEnd + 2;
    const end = start + size;
    if (buffer.length < end + 2) return null;
    return {
      value: buffer.subarray(start, end).toString("utf8"),
      rest: buffer.subarray(end + 2),
    };
  }
  throw new Error(`Unsupported Redis reply type ${type}`);
}

async function redisPipeline(
  commands: readonly (readonly string[])[],
): Promise<RespValue[]> {
  const settings = loadRedisSettings();
  return new Promise((resolve, reject) => {
    const socket = createConnection({
      host: settings.host,
      port: settings.port,
    });
    let buffer = Buffer.alloc(0);
    const replies: RespValue[] = [];
    const expected = commands.length;

    const fail = (error: Error) => {
      socket.destroy();
      reject(error);
    };

    socket.setTimeout(5000);
    socket.once("timeout", () =>
      fail(new Error(`Redis timed out at ${settings.host}:${settings.port}`)),
    );
    socket.once("error", fail);
    socket.once("connect", () => {
      socket.write(Buffer.concat(commands.map(encodeCommand)));
    });
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      try {
        while (replies.length < expected) {
          const parsed = tryParseReply(buffer);
          if (!parsed) return;
          replies.push(parsed.value);
          buffer = parsed.rest;
        }
        socket.end();
        resolve(replies);
      } catch (error) {
        fail(error instanceof Error ? error : new Error(String(error)));
      }
    });
    socket.once("end", () => {
      if (replies.length < expected) {
        fail(
          new Error(
            `Redis closed after ${replies.length}/${expected} replies`,
          ),
        );
      }
    });
  });
}

export async function redisGet(key: string): Promise<string | null> {
  const settings = loadRedisSettings();
  const commands: string[][] = [];
  if (settings.password) {
    commands.push(["AUTH", settings.password]);
  }
  if (settings.db !== 0) {
    commands.push(["SELECT", String(settings.db)]);
  }
  commands.push(["GET", key]);
  const replies = await redisPipeline(commands);
  const value = replies.at(-1);
  return typeof value === "string" && value.length > 0 ? value : null;
}

export async function readCaptchaCode(token: string): Promise<string> {
  const code = await redisGet(`captcha:${token}`);
  if (!code) {
    throw new Error(
      `CAPTCHA token ${token.slice(0, 8)}… was not in Redis. Point REDIS_HOST at the same instance FastAPI uses.`,
    );
  }
  return code;
}
