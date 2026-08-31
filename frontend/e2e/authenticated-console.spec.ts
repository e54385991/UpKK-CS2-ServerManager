import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { sessionBearer } from "./helpers/auth";

/**
 * Authenticated happy paths for the Next replacement console.
 * Read-only: never start/stop/deploy/force-stop, and never start SteamCMD.
 * Server 2 (lan-ops) may have a live deploy — inspect current op first.
 */

const WORKSPACE_NAV = /服务器分类|Server categories/;
const TRANSFER_OPEN = /导入 \/ 导出|Import \/ export/;

async function closeDialog(page: Page) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
}

test.describe.configure({ timeout: 45_000 });

test("overview greets the admin and shows fleet stats", async ({ page }) => {
  await page.goto("/overview");
  await expect(
    page.getByRole("heading", { name: /欢迎回来，admin|Welcome back, admin/ }),
  ).toBeVisible();
  await expect(page.getByText(/服务器总数|Total servers/)).toBeVisible();
  await expect(page.getByRole("navigation").getByText(/总览|Overview/).first()).toBeVisible();
  await expect(
    page.getByRole("link", { name: /打开部署教程|Open the deployment tutorial/ }),
  ).toBeVisible();
  await expect(page.getByTestId("activity-tray-toggle")).toBeVisible();
});

test("overview tutorial keeps the sidebar and can scroll to the last step", async ({
  page,
}) => {
  await page.goto("/overview");
  await page
    .getByRole("link", { name: /打开部署教程|Open the deployment tutorial/ })
    .click();
  await expect(page).toHaveURL(/\/deployment-tutorial$/);
  await expect(page.getByTestId("tutorial-guide")).toBeVisible();
  await expect(
    page.getByRole("navigation").getByText(/总览|Overview/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /返回总览|Back to overview/ }),
  ).toBeVisible();

  const lastStep = page.getByTestId("tutorial-step-10");
  await lastStep.scrollIntoViewIfNeeded();
  await expect(lastStep).toBeInViewport();
});

test("activity tray shows queue and failed tabs", async ({ page }) => {
  await page.goto("/overview");
  const toggle = page.getByTestId("activity-tray-toggle");
  await expect(toggle).toBeVisible();
  if ((await page.getByTestId("activity-tray-count").count()) > 0) {
    await expect(toggle).toHaveAttribute("data-busy", "true");
    await expect(toggle).toContainText(/剩余|remaining/i);
  }
  await toggle.click();
  await expect(page.getByTestId("activity-tray-panel")).toBeVisible();
  await expect(page.getByTestId("activity-tray-tab-queue")).toBeVisible();
  await expect(page.getByTestId("activity-tray-tab-failed")).toBeVisible();
  if ((await page.getByTestId("activity-command").count()) > 0) {
    await expect(page.getByTestId("activity-command")).toBeVisible();
    await expect(page.getByTestId("activity-status")).toBeVisible();
  }
  await page.getByTestId("activity-tray-tab-failed").click();
  await expect(page.getByTestId("activity-tray-tab-failed")).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByText(/失败任务默认保留 7 天|Failed tasks are kept for 7 days/)).toBeVisible();
});

test("servers list exports a redacted configuration bundle", async ({
  page,
}) => {
  await page.goto("/servers");
  await expect(page.getByRole("heading", { name: /服务器|Servers/ })).toBeVisible();
  await expect(page.getByText("ops-verify")).toBeVisible();
  await expect(page.getByText("lan-ops")).toBeVisible();

  await page.getByRole("button", { name: TRANSFER_OPEN }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("heading", {
      name: /导入 \/ 导出配置|Import \/ export configuration/,
    }),
  ).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await dialog
    .getByRole("button", { name: /导出脱敏副本|Export redacted copy/ })
    .click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/cs2-server-config-.*\.json/);
  const filePath = await download.path();
  expect(filePath).toBeTruthy();
  const bundle = JSON.parse(await readFile(filePath!, "utf8")) as {
    format?: string;
    include_secrets?: boolean;
    servers?: Array<Record<string, unknown>>;
  };
  expect(bundle.format).toBe("upkk-cs2-server-config");
  expect(bundle.include_secrets).toBe(false);
  expect(bundle.servers?.length).toBeGreaterThan(0);
  for (const server of bundle.servers ?? []) {
    expect(server.ssh_password).toBeNull();
    expect(server.sudo_password).toBeNull();
    expect(server.rcon_password).toBeNull();
    expect(server.steam_account_token).toBeNull();
    expect(server.discord_webhook_url).toBeNull();
    expect(server.server_password).toBeNull();
  }
  await expect(dialog.getByRole("status")).toContainText(/脱敏|redacted/i);
  await closeDialog(page);
});

test("server workspace two-row nav reaches named surfaces", async ({ page }) => {
  await page.goto("/servers/1");
  const sshCard = page.getByTestId("workspace-ssh-card");
  await expect(sshCard).toBeVisible();
  await expect(sshCard.getByTestId("workspace-action-restart")).toBeVisible();
  await expect(sshCard.getByTestId("workspace-action-stop")).toBeVisible();
  await expect(sshCard.getByTestId("workspace-action-update")).toBeVisible();
  const nav = page.getByRole("navigation", { name: WORKSPACE_NAV });
  await expect(nav).toBeVisible();
  await expect(nav.getByText(/运行|Run/)).toBeVisible();
  await expect(nav.getByText(/主机|Host/)).toBeVisible();

  await nav.getByRole("link", { name: /操作中心|Operations center/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/operations$/);
  await expect(
    page.getByRole("heading", { name: /自定义快捷命令|Custom quick commands/ }),
  ).toBeVisible();
  await expect(page.getByTestId("cleanup-console")).toHaveCount(0);

  await nav.getByRole("link", { name: /日志与垃圾清理|Log & junk cleanup/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/cleanup$/);
  await expect(page.getByTestId("cleanup-console")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /日志与垃圾清理|Log & junk cleanup/ }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^扫描$|^Scan$/ })).toBeVisible();

  await nav.getByRole("link", { name: /S3 备份|S3 backups/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/backups$/);

  await nav.getByRole("link", { name: /^插件$|^Plugins$/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/plugins$/);
  await expect(
    page.getByRole("heading", { name: /从 GitHub 安装插件|Install plugin from GitHub/ }),
  ).toBeVisible();
  await expect(
    page.getByLabel(/GitHub 仓库地址|GitHub repository URL/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /获取发行版|Fetch releases/ }),
  ).toBeVisible();
  await expect(page.getByTestId("github-exclude-toggles")).toBeVisible();
  await expect(page.getByTestId("github-file-mapping")).toBeVisible();
  await expect(page.getByTestId("github-uninstall")).toBeVisible();
  await expect(page.getByText(/排除目录或文件|Exclude directories or files/)).toBeVisible();
  await expect(page.getByText(/压缩包文件映射|Archive file mapping/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /卸载所选文件|Uninstall selected files/ }),
  ).toBeVisible();

  await nav.getByRole("link", { name: /插件配置|Plugin configs/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/plugin-configs$/);
  await expect(
    page.getByRole("heading", { name: /插件配置|Plugin configuration/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/只有点击|Sources are not scanned/),
  ).toBeVisible();

  await nav.getByRole("link", { name: /自动更新|Auto-update/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/updates$/);
  await expect(
    page
      .getByRole("heading", { name: /CS2 游戏版本|CS2 game version/ })
      .or(page.getByText(/暂时无法|Unable to load/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("plugin-exclude-fields")
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();
  await expect(
    page
      .getByText(/排除目录|Exclude directories/)
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();
  await expect(
    page
      .getByText(/排除文件|Exclude files/)
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("plugin-register-form")
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("plugin-unregister")
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();
  await expect(
    page
      .getByRole("button", {
        name: /登记（自动更新保持关闭）|Register \(auto-update remains off\)/,
      })
      .or(page.getByText(/暂时无法加载自动更新|Unable to load auto-update/)),
  ).toBeVisible();

  await nav.getByRole("link", { name: /^地图$|^Maps$/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/maps$/);
  await expect(
    page
      .getByRole("heading", { name: /MapChooser|地图池|Map pool/ })
      .first()
      .or(page.getByText(/暂时无法加载地图|Unable to load the maps/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("mapchooser-uninstall")
      .or(page.getByText(/暂时无法加载地图|Unable to load the maps/)),
  ).toBeVisible();

  await nav.getByRole("link", { name: /^文件$|^Files$/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/files$/);
  await expect(
    nav.getByRole("link", { name: /^文件$|^Files$/ }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    page
      .getByTestId("files-path-input")
      .or(page.getByText(/暂时无法加载文件|Unable to load the files/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("files-path-copy")
      .or(page.getByText(/暂时无法加载文件|Unable to load the files/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("files-path-parent")
      .or(page.getByText(/暂时无法加载文件|Unable to load the files/)),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("files-path-go")
      .or(page.getByText(/暂时无法加载文件|Unable to load the files/)),
  ).toBeVisible();

  await nav.getByRole("link", { name: /^控制台$|^Console$/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/console$/);
  await expect(
    nav.getByRole("link", { name: /^控制台$|^Console$/ }),
  ).toHaveAttribute("aria-current", "page");

  await nav.getByRole("link", { name: /^帮助$|^Help$/ }).click();
  await expect(page).toHaveURL(/\/servers\/1\/help$/);
  await expect(
    page.getByRole("heading", {
      name: /帮助与故障排除|Help & troubleshooting/,
    }),
  ).toBeVisible();
});

test("console workspace offers ssh and game popups; deploy only while deploying", async ({
  page,
  context,
}) => {
  await page.goto("/servers/1/console");
  const ssh = page.getByTestId("open-live-ssh").first();
  const game = page.getByTestId("open-live-game").first();
  const deploy = page.getByTestId("open-live-deploy");
  await expect(ssh).toBeVisible();
  await expect(game).toBeVisible();
  await expect(ssh).toHaveText(/打开 SSH 终端|Open SSH console/);
  await expect(game).toHaveText(/打开游戏控制台|Open game console/);

  const steamcmdRunning = await page
    .getByText(/SteamCMD 会话在运行|SteamCMD session running/)
    .isVisible();
  if (steamcmdRunning) {
    await expect(deploy.first()).toBeVisible();
    await expect(deploy.first()).toHaveText(/打开部署进度|Open deploy progress/);
  } else {
    await expect(deploy).toHaveCount(0);
  }

  const views = steamcmdRunning
    ? ([
        { button: ssh, view: "ssh" },
        { button: game, view: "game" },
        { button: deploy.first(), view: "deploy" },
      ] as const)
    : ([
        { button: ssh, view: "ssh" },
        { button: game, view: "game" },
      ] as const);
  for (const item of views) {
    const popupPromise = context.waitForEvent("page");
    await item.button.click();
    const popup = await popupPromise;
    await expect(popup).toHaveURL(new RegExp(`/live-console/1\\?view=${item.view}`));
    await popup.close();
  }
});

test("operations center is read-only and does not start SteamCMD", async ({
  page,
}) => {
  const token = await sessionBearer(page);
  const current = await page.request.get("/api/v1/servers/2/operations/current", {
    headers: { authorization: `Bearer ${token}` },
  });
  expect(current.ok()).toBeTruthy();
  const body = (await current.json()) as {
    operation?: { status?: string; action?: string } | null;
  };
  const live = body.operation;
  const lanOpsBusy =
    live != null && (live.status === "queued" || live.status === "running");

  // Prefer the idle verify host for the action chrome. lan-ops is inspected
  // only when a deploy is already live — still no clicks on start/deploy/stop.
  await page.goto("/servers/1/operations");
  await expect(
    page.getByRole("heading", { name: /操作中心|Operations center/ }),
  ).toBeVisible();
  await expect(page.getByTestId("open-live-deploy")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: /最近操作|Recent operations/ }),
  ).toBeVisible();
  const deploy = page.getByRole("button", { name: /部署|Deploy/ }).first();
  await expect(deploy).toBeVisible();
  // Never click deploy / start / force-stop.

  if (lanOpsBusy) {
    await page.goto("/servers/2/operations");
    await expect(page.getByRole("heading", { name: /lan-ops/ })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /操作中心|Operations center/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /部署|Deploy/ }).first(),
    ).toBeDisabled();
  }
});

test("server config splits game and host into two categories", async ({
  page,
}) => {
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("server-config-tabs")).toBeVisible();
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  await expect(page.locator("#serverName")).toBeVisible();
  await expect(page.getByTestId("gslt-field")).toBeVisible();
  await expect(page.getByTestId("additional-parameters")).toBeVisible();
  await expect(
    page.getByText(/host_workshop_map/),
  ).toBeVisible();
  await page.getByTestId("additional-parameters-use-ze").click();
  await expect(page.getByTestId("additional-parameters-input")).toHaveValue(
    /host_workshop_map 3171881962/,
  );
  await expect(page.getByTestId("gslt-generate")).toBeVisible();
  await page.getByTestId("gslt-generate").click();
  await expect(page.getByTestId("gslt-dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: /关闭|Close/ }).first().click();
  await expect(page.getByTestId("apply-system-defaults")).toHaveCount(0);
  await expect(page.locator("#host")).toHaveCount(0);

  await page.getByTestId("server-config-tabs").getByRole("link", {
    name: /主机配置|Host config/,
  }).click();
  await expect(page).toHaveURL(/\/servers\/1\/host-config$/);
  await expect(page.getByTestId("host-config-form")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Linux 主机|Linux host/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /下载代理|Download proxy/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/面板服务器代理|Panel server proxy/),
  ).toBeVisible();
  await expect(page.getByTestId("apply-system-defaults")).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: /应用系统代理默认值|Apply system proxy defaults/,
    }),
  ).toBeVisible();
});

test("server overview shows a masked startup command", async ({ page }) => {
  await page.goto("/servers/1");
  await expect(
    page.getByRole("heading", { name: /启动命令|Startup command/ }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /复制|Copy/ })).toBeVisible();
});

test("s3 backups page lists without restoring", async ({ page }) => {
  await page.goto("/servers/1/backups");
  await expect(
    page.getByRole("heading", { name: /S3 备份|S3 backups/ }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /没有找到 S3 备份|No S3 backups found|尚未配置 S3|not configured|暂时无法加载 S3|Unable to load S3/,
    ),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /刷新|Refresh/ })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /手动备份插件|Back up plugins now/ }),
  ).toBeVisible();
  await expect(page.getByTestId("s3-local-backups-dir")).toBeVisible();
});

test("admin fleet toggle stays on the servers list", async ({ page }) => {
  await page.goto("/servers");
  const fleet = page.getByRole("link", { name: /全部用户|All users/ });
  await expect(page.getByRole("link", { name: /我的服务器|My servers/ })).toBeVisible();
  await expect(fleet).toBeVisible();
  await expect(
    page.getByRole("button", { name: /刷新磁盘空间|Refresh disk space/ }),
  ).toBeVisible();
  await expect(page.getByText(/磁盘空间|Disk space/).first()).toBeVisible();
  await expect(page.getByTestId("fleet-bulk-bar")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /导出所选|Export selected/ }),
  ).toBeVisible();
  await expect(page.getByTestId("a2s-overlay").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /管理服务器|Manage server/ }).first()).toBeVisible();
  await expect(page.getByTestId("ssh-health").first()).toBeVisible();
  await expect(page.getByTestId("ssh-health-badge").first()).toBeVisible();
  await expect(
    page.getByText(/SSH 连通性|SSH connectivity/).first(),
  ).toBeVisible();
  await expect(
    page.getByText(/等待缓存中的服务器信息|Waiting for cached server info|服务器在线|Server online/).first(),
  ).toBeVisible();
  await fleet.click();
  await expect(page).toHaveURL(/scope=all/);
  await expect(page.getByText("ops-verify")).toBeVisible();
  await expect(page.getByText("lan-ops")).toBeVisible();
});

test("plugin catalog dialog opens without importing", async ({ page }) => {
  await page.goto("/plugins");
  await expect(page.getByRole("heading", { name: /插件中心|Plugins/ })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /从 GitHub 安装|Install from GitHub/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /从 GitHub 安装插件|Install plugin from GitHub/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: TRANSFER_OPEN }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("heading", {
      name: /导入 \/ 导出插件目录|Import \/ export plugin catalog/,
    }),
  ).toBeVisible();
  await expect(
    dialog.getByText(/导出目录|Export catalog|市场为空|market is empty|暂时无法|Unable to load/),
  ).toBeVisible();
  await closeDialog(page);

  await page.getByRole("button", { name: /从 GitHub 安装|Install from GitHub/ }).click();
  const github = page.getByRole("dialog");
  await expect(github).toBeVisible();
  await expect(
    github.getByRole("heading", {
      name: /从 GitHub 安装插件|Install plugin from GitHub/,
    }),
  ).toBeVisible();
  await expect(
    github.getByLabel(/GitHub 仓库地址|GitHub repository URL/),
  ).toBeVisible();
  await expect(github.getByTestId("github-exclude-toggles")).toBeVisible();
  await expect(github.getByTestId("github-file-mapping")).toBeVisible();
  await closeDialog(page);

  const cardInstall = page.getByTestId("market-install-open");
  if ((await cardInstall.count()) > 0) {
    await expect(page.getByTestId("market-delete").first()).toBeVisible();
    await cardInstall.first().click();
    const market = page.getByTestId("market-install-dialog");
    await expect(market).toBeVisible();
    await expect(market.getByTestId("market-install-form")).toBeVisible();
    await expect(market.getByText(/升级模式|Upgrade mode/)).toBeVisible();
    await expect(
      market.getByText(/同时安装依赖|Install dependencies/),
    ).toBeVisible();
    await closeDialog(page);
  }
});

test("settings and profile render parity fields", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: /系统设置|Settings/ })).toBeVisible();
  await expect(page.getByText(/下载代理|Download proxy/)).toBeVisible();
  await expect(page.getByText(/全局 GitHub Token|Global GitHub token/)).toBeVisible();
  await expect(page.getByText(/密码重置|password resets|Outbound mail/)).toBeVisible();
  await expect(page.getByTestId("ai-settings-card")).toBeVisible();
  await expect(page.getByTestId("ai-settings-test")).toBeVisible();

  await page.goto("/settings/profile");
  await expect(
    page.getByRole("heading", { level: 1, name: /个人中心|Account/ }),
  ).toBeVisible();
  await expect(page.getByText(/GitHub 个人访问令牌|GitHub personal access token/)).toBeVisible();
  await expect(page.getByText(/SteamCMD 自动恢复|SteamCMD auto-recovery/)).toBeVisible();
  // S3 is rendered when GET /api/v1/profile/s3 succeeds; stale live APIs omit it.
});

test("host auto-setup wizard is visible and is not submitted", async ({
  page,
}) => {
  await page.goto("/servers");
  await expect(page.getByRole("link", { name: /主机初始化|Initialize host/ })).toBeVisible();
  await page.goto("/servers/new?tab=setup");
  await expect(page.getByTestId("setup-wizard")).toBeVisible();
  await expect(page.getByTestId("new-server-tabs")).toBeVisible();
  await expect(page.getByTestId("setup-mode-auto")).toBeVisible();
  await expect(page.getByTestId("setup-mode-manual")).toBeVisible();
  await expect(page.getByLabel(/名称|Name/).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: /开始自动设置|Start automatic setup/ }),
  ).toBeVisible();
  await page.getByTestId("setup-mode-manual").click();
  await expect(page.getByTestId("setup-manual-script")).toBeVisible();
  await expect(page.getByText(/setup_cs2\.sh/)).toBeVisible();
});

test("monitoring shows plugin diagnostics without executing", async ({
  page,
}) => {
  await page.goto("/servers/1/monitoring");
  await expect(page.getByTestId("plugin-diagnostics")).toBeVisible();
  await expect(
    page.getByTestId("plugin-diagnostics-idle").or(page.getByTestId("open-diagnostic-assistant")),
  ).toBeVisible();
  await expect(page.getByTestId("diagnostic-plan")).toBeVisible();
  await expect(page.getByTestId("diagnostic-execute")).toBeVisible();
  await expect(page.getByTestId("diagnostic-restore")).toBeVisible();
  await expect(page.getByTestId("a2s-panel")).toBeVisible();
  await expect(page.getByTestId("a2s-query-host")).toBeVisible();
  await expect(page.getByTestId("a2s-query-port")).toBeVisible();
  await expect(page.getByTestId("a2s-query-now")).toBeVisible();
  await expect(page.getByTestId("a2s-last-check")).toBeVisible();
  // Do not click plan/execute/restore — plan talks to SSH.
});

test("profile steamcmd retries can be saved and restored", async ({ page }) => {
  await page.goto("/settings/profile");
  const retries = page.getByLabel(/最大自动恢复次数|Maximum recovery attempts/);
  await expect(retries).toBeVisible();
  const original = await retries.inputValue();
  const nextValue = original === "20" ? "19" : "20";
  const form = page.locator("form").filter({ has: retries });

  await retries.fill(nextValue);
  await form.getByRole("button", { name: /^保存$|^Save$/ }).click();
  await expect(form.getByRole("status")).toContainText(/已保存|Saved/);
  await expect(retries).toHaveValue(nextValue);

  await retries.fill(original);
  await form.getByRole("button", { name: /^保存$|^Save$/ }).click();
  await expect(form.getByRole("status")).toContainText(/已保存|Saved/);
  await expect(retries).toHaveValue(original);
});

test("destructive actions use in-app confirm instead of window dialogs", async ({
  page,
}) => {
  const nativeDialogs: string[] = [];
  page.on("dialog", (dialog) => {
    nativeDialogs.push(dialog.type());
    void dialog.dismiss();
  });

  await page.goto("/servers/1/operations");
  await page.getByTestId("workspace-action-stop").click();
  const confirm = page.getByTestId("app-confirm");
  await expect(confirm).toBeVisible();
  await expect(
    confirm.getByRole("heading", { name: /请确认|Please confirm/ }),
  ).toBeVisible();
  await confirm.getByRole("button", { name: /取消|Cancel/ }).click();
  await expect(confirm).toHaveCount(0);

  await page.getByTestId("operations-action-stop").click();
  await expect(confirm).toBeVisible();
  await expect(
    page.getByRole("region", { name: /Notifications/i }),
  ).toBeAttached();
  await confirm.getByRole("button", { name: /取消|Cancel/ }).click();
  await expect(confirm).toHaveCount(0);
  expect(nativeDialogs).toEqual([]);
});
