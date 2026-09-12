import { test, expect, type BrowserContext, type APIRequestContext } from '@playwright/test';
import en from '../src/i18n/messages/en-US.json' with { type: 'json' };
import zh from '../src/i18n/messages/zh-CN.json' with { type: 'json' };
import { mkdir, writeFile } from 'node:fs/promises';
const mock = `http://127.0.0.1:${process.env.PERF_MOCK_PORT ?? '38131'}`;
async function login(context: BrowserContext, actor = 'admin', locale = 'en-US') {
  await context.addCookies([
    { name: 'upkk_access_token', value: `fixture-${actor}`, domain: '127.0.0.1', path: '/' },
    { name: 'locale', value: locale, domain: '127.0.0.1', path: '/' },
  ]);
}
async function state(request: APIRequestContext) { return (await request.get(`${mock}/__test__/state`)).json(); }

for (const route of ['/plugins', '/plugins/1', '/servers/1/config', '/servers/1/files', '/assistant?conversation=conversation-1']) {
  test(`production measurement ${route}`, async ({ page, context, request }, info) => {
    await request.post(`${mock}/__test__/reset`, { data: { rules: {
      '/api/v1/servers': { delay: 150 }, '/api/v1/plugins/market': { delay: 150 }, '/api/v1/plugins/market/1': { delay: 150 },
    } } });
    await login(context);
    const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      (window as Window & { shifts: number }).shifts = 0;
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) if (!(entry as PerformanceEntry & { hadRecentInput: boolean }).hadRecentInput)
          (window as Window & { shifts: number }).shifts += (entry as PerformanceEntry & { value: number }).value;
      }).observe({ type: 'layout-shift', buffered: true });
    });
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible();
    if (route.startsWith('/plugins')) await expect(page.getByText('Fixture Plugin', { exact: true }).first()).toBeVisible();
    if (route.endsWith('/files')) await expect(page.getByText('server.cfg', { exact: true }).first()).toBeVisible();
    await page.waitForLoadState('load');
    const metrics = await page.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
      return { ttfb: nav.responseStart, domContentLoaded: nav.domContentLoadedEventEnd, load: nav.loadEventEnd,
        cls: (window as Window & { shifts: number }).shifts,
        scripts: performance.getEntriesByType('resource').filter(e => e.name.includes('/_next/static/') && new URL(e.name).pathname.endsWith('.js')).length };
    });
    const snapshot = await state(request);
    const result = { route, metrics, requests: snapshot.requests };
    await info.attach('measurement', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
    await mkdir('test-results/performance', { recursive: true });
    await writeFile(`test-results/performance/${process.env.PERF_LABEL ?? 'after'}-${route.replace(/\W/g, '_')}.json`, JSON.stringify(result, null, 2));
    expect(errors).toEqual([]);
  });
}

for (const locale of ['en-US', 'zh-CN']) for (const actor of ['admin', 'member']) {
  test(`${locale} ${actor}: request-scoped snapshots and fresh reload`, async ({ page, context, request }) => {
    await login(context, actor, locale);
    for (let pass = 0; pass < 2; pass++) {
      await request.post(`${mock}/__test__/reset`);
      if (!pass) await page.goto('/servers/1/config'); else await page.reload();
      await expect(page.getByTestId('workspace-status')).toBeVisible();
      await expect(page.locator('h1')).toContainText('fixture-server-1');
      const records = (await state(request)).requests;
      for (const path of ['/api/v1/auth/me', '/api/v1/servers/1', '/api/v1/servers/1/operations/current', '/api/v1/servers/1/operations/lock']) {
        expect(records.filter((r: { path: string }) => r.path === path), path).toHaveLength(1);
      }
      expect(records.find((r: { path: string }) => r.path === '/api/v1/auth/me').actor).toBe(`Bearer fixture-${actor}`);
    }
  });
}

for (const navigation of ['initial', 'client']) for (const route of ['/plugins', '/plugins/1']) {
  test(`${navigation} ${route}: parallel fetch before servers resolve`, async ({ page, context, request }) => {
    await login(context);
    if (navigation === 'client') await page.goto(route === '/plugins' ? '/deployment-tutorial' : '/plugins');
    await request.post(`${mock}/__test__/reset`, { data: { rules: { '/api/v1/servers': { gate: true } } } });
    if (navigation === 'initial') await page.goto(route, { waitUntil: 'commit' });
    else {
      await page.evaluate(() => { (window as Window & { marker: string }).marker = 'same-document'; });
      await page.locator(`a[href="${route}"]`).first().click();
    }
    const path = route === '/plugins' ? '/api/v1/plugins/market' : '/api/v1/plugins/market/1';
    await expect.poll(async () => (await state(request)).requests.some((r: { path: string }) => r.path === path)).toBe(true);
    expect((await state(request)).requests.find((r: { path: string }) => r.path === '/api/v1/servers').ended).toBeNull();
    await request.post(`${mock}/__test__/release`, { data: { path: '/api/v1/servers' } });
    await expect(page.getByText('Fixture Plugin', { exact: true }).first()).toBeVisible();
    if (navigation === 'client') expect(await page.evaluate(() => (window as Window & { marker: string }).marker)).toBe('same-document');
  });
}

for (const locale of ['zh-CN', 'en-US']) for (const status of [200, 503]) {
  test(`${locale} state ${status}: slow lock cannot block workspace`, async ({ page, context, request }) => {
    await login(context, 'admin', locale);
    const path = '/api/v1/servers/1/operations/lock';
    await request.post(`${mock}/__test__/reset`, { data: { rules: { [path]: { gate: true, ...(status === 503 ? { status } : {}) } } } });
    await page.goto('/servers/1/config', { waitUntil: 'commit' });
    await expect(page.locator('h1')).toContainText('fixture-server-1');
    await expect(page.getByTestId('workspace-status-loading')).toBeVisible();
    await expect(page.locator('main input').first()).toBeVisible();
    await expect(page.getByTestId('workspace-action-stop')).toHaveCount(0);
    const loadingBox = await page.getByTestId('workspace-status-loading').boundingBox();
    await request.post(`${mock}/__test__/release`, { data: { path } });
    await expect(page.getByTestId('workspace-status')).toBeVisible();
    await expect(page.getByTestId('workspace-status-loading')).toHaveCount(0);
    await expect(page.getByTestId('workspace-action-stop')).toBeVisible();
    console.log('status geometry', locale, loadingBox, await page.getByTestId('workspace-ssh-card').boundingBox());
  });
}

test('assistant fetches detail and authorized servers while workspace waits', async ({ page, context, request }) => {
  await login(context, 'member');
  await request.post(`${mock}/__test__/reset`, { data: { rules: { '/api/v1/assistant': { gate: true } } } });
  await page.goto('/assistant?conversation=conversation-1', { waitUntil: 'commit' });
  for (const path of ['/api/v1/assistant/conversations/conversation-1', '/api/v1/servers']) {
    await expect.poll(async () => (await state(request)).requests.some((r: { path: string }) => r.path === path)).toBe(true);
  }
  expect((await state(request)).requests.find((r: { path: string }) => r.path === '/api/v1/servers').query).toBe('');
  await request.post(`${mock}/__test__/release`, { data: { path: '/api/v1/assistant' } });
  await expect(page.getByText('Fixture conversation', { exact: true }).first()).toBeVisible();
});

test('resource ids, permissions, expired sessions and timeout stay isolated', async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`);
  await page.goto('/servers/2/config');
  await expect(page.locator('h1')).toContainText('fixture-server-2');
  await page.goto('/servers/404/config');
  await expect(page.getByText('404', { exact: true })).toBeVisible();
  await login(context, 'member');
  await page.goto('/servers/2/config');
  await expect(page.locator('main')).not.toContainText('fixture-server-2');
  await login(context, 'invalid');
  await page.goto('/plugins');
  await expect(page).toHaveURL(/\/login$/);
  await login(context);
  await request.post(`${mock}/__test__/reset`, { data: { rules: { '/api/v1/servers/1': { gate: true } } } });
  await page.goto('/servers/1/config', { waitUntil: 'commit' });
  await expect(page.locator('main')).toContainText('Unable to load this server', { timeout: 12_000 });
  expect((await state(request)).requests.filter((r: { path: string }) => r.path === '/api/v1/servers/1')).toHaveLength(1);
});

for (const locale of ['en-US', 'zh-CN']) {
  test(`${locale}: lazy market forms keep reopening behavior`, async ({ page, context, request }) => {
    await login(context, 'admin', locale);
    await request.post(`${mock}/__test__/reset`);
    await page.goto('/plugins');
    await page.getByTestId('market-create-open').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('dialog').locator('input').first().fill('https://github.com/fixture/draft');
    await page.keyboard.press('Escape');
    await page.getByTestId('market-create-open').click();
    await expect(page.getByRole('dialog').locator('input').first()).toHaveValue('');
    await page.keyboard.press('Escape');
    await page.getByTestId('market-edit-open').click();
    await expect(page.getByRole('dialog').locator('input').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByTestId('market-install-open').click();
    await expect(page.locator('#install-server-1')).toBeVisible();
    await page.locator('#install-server-1').selectOption('2');
    await page.keyboard.press('Escape');
    await page.getByTestId('market-install-open').click();
    await expect(page.locator('#install-server-1')).toHaveValue('1');
    await page.keyboard.press('Escape');
  });
}

test('Next runtime has no compilation or execution errors', async ({ page, context, request }) => {
  test.skip(process.env.PERF_PRODUCTION === '1', 'Next diagnostics are dev-only');
  await request.post(`${mock}/__test__/reset`);
  await login(context); await page.goto('/plugins');
  const rpc = async (method: string, params = {}) => {
    const response = await page.request.post('/_next/mcp', { headers: { accept: 'application/json, text/event-stream' }, data: { jsonrpc: '2.0', id: 1, method, params } });
    const body = await response.text(); const line = body.split('\n').find(line => line.startsWith('data: '));
    return JSON.parse(line ? line.slice(6) : body);
  };
  const listed = await rpc('tools/list');
  expect(listed.result.tools.map((tool: { name: string }) => tool.name)).toContain('get_compilation_issues');
  for (const name of ['get_compilation_issues', 'get_errors']) {
    const result = await rpc('tools/call', { name, arguments: {} });
    expect(result.error).toBeUndefined();
    expect(JSON.parse(result.result.content[0].text)).toEqual(name === 'get_errors' ? { configErrors: [], sessionErrors: [] } : { issues: [] });
  }
});

for (const locale of ['en-US', 'zh-CN']) {
  test(`${locale}: lazy file dialogs preserve editing, unsaved protection and save`, async ({ page, context, request }) => {
    const m = locale === 'en-US' ? en : zh;
    await request.post(`${mock}/__test__/reset`); await login(context, 'admin', locale);
    await page.goto('/servers/1/files');
    const row = page.getByRole('row').filter({ has: page.getByText('server.cfg', { exact: true }) });
    await row.getByRole('button', { name: m.files.rename, exact: true }).click();
    await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('server.cfg');
    await page.keyboard.press('Escape');
    await row.getByRole('button', { name: m.files.edit, exact: true }).click();
    const editor = page.locator('.cm-content');
    await expect(editor).toBeVisible();
    await editor.fill('hostname "changed"');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('alertdialog', { name: m.files.editUnsavedTitle })).toBeVisible();
    await page.getByRole('alertdialog', { name: m.files.editUnsavedTitle }).getByRole('button', { name: m.feedback.cancel, exact: true }).click();
    await expect(editor).toContainText('changed');
    await page.getByRole('button', { name: m.files.saveFile, exact: true }).click();
    await expect(editor).toHaveCount(0);
    expect((await state(request)).savedContent).toBe('hostname "changed"');
    await page.getByTestId('files-extract-plugin.zip').click();
    await expect(page.getByRole('dialog', { name: m.files.extractTitle })).toBeVisible();
    await expect(page.getByTestId('files-extract-start')).toBeEnabled();
    await page.keyboard.press('Escape');
  });

  test(`${locale}: catalog draft, GitHub form and install confirmation survive lazy loading`, async ({ page, context, request }) => {
    const m = locale === 'en-US' ? en : zh;
    await request.post(`${mock}/__test__/reset`); await login(context, 'admin', locale);
    await page.goto('/plugins');
    await page.getByRole('button', { name: m.plugins.catalog.open, exact: true }).click();
    const catalog = page.getByRole('dialog', { name: m.plugins.catalog.title });
    await catalog.getByRole('tab', { name: m.plugins.catalog.importTitle, exact: true }).click();
    await catalog.getByRole('combobox').selectOption('update');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: m.plugins.catalog.open, exact: true }).click();
    await expect(catalog.getByRole('combobox')).toHaveValue('update');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: m.plugins.github.open, exact: true }).click();
    await expect(page.getByRole('dialog').getByLabel(m.plugins.github.repoUrl, { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByTestId('market-install-open').click();
    const installDialog = page.getByRole('dialog');
    await expect(installDialog.locator('#install-version-1')).toHaveValue('0');
    await installDialog.getByRole('button', { name: m.plugins.checkPlan, exact: true }).click();
    await expect(installDialog.getByRole('button', { name: m.plugins.install, exact: true })).toBeEnabled();
    await installDialog.getByRole('button', { name: m.plugins.install, exact: true }).click();
    const warning = page.getByRole('alertdialog', { name: m.plugins.frameworkMismatchTitle });
    await expect(warning).toBeVisible();
    expect((await state(request)).requests.filter((r: { path: string }) => r.path.endsWith('/install'))).toHaveLength(0);
    await warning.getByRole('button', { name: m.feedback.cancel, exact: true }).click();
    await installDialog.getByRole('button', { name: m.plugins.install, exact: true }).click();
    await warning.getByRole('button', { name: m.plugins.install, exact: true }).click();
    await expect.poll(async () => (await state(request)).requests.find((r: { path: string }) => r.path.endsWith('/plugins/market/1/install'))?.input.acknowledge_framework_mismatch).toBe(true);
    await expect(page.getByTestId('activity-tray-toggle')).toBeVisible();
  });
}

test('editing a listing sees fresh session and updated data after mutation', async ({ page, context, request }) => {
  await request.post(`${mock}/__test__/reset`); await login(context);
  await page.goto('/plugins');
  await page.getByTestId('market-edit-open').click();
  await page.getByRole('dialog').getByLabel(en.plugins.create.titleField, { exact: true }).fill('Updated fixture');
  await page.getByRole('dialog').getByRole('button', { name: en.plugins.edit.submit, exact: true }).click();
  await expect(page.getByRole('link', { name: 'Updated fixture', exact: true })).toBeVisible();
  const requests = (await state(request)).requests;
  expect(requests.filter((r: { path: string, method: string }) => r.path === '/api/v1/plugins/market/1' && r.method === 'PATCH')).toHaveLength(1);
  expect(requests.filter((r: { path: string }) => r.path === '/api/v1/auth/me').length).toBeGreaterThan(1);
});
