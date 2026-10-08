// Public URLs are deliberately independent of the internal [locale] segment.
export const CACHE_COMPONENTS_SURFACES = [
  '/', '/login', '/register', '/forgot-password', '/reset-password?token=fixture',
  '/google-callback', '/deployment-tutorial', '/overview', '/servers', '/servers/new',
  '/servers/initialized', '/servers/1',
  ...['additional-fixes', 'backups', 'cleanup', 'config', 'console', 'discord', 'files',
    'frameworks', 'game-modes', 'help', 'host-config', 'maps', 'monitoring', 'operations',
    'plugin-configs', 'plugins', 'schedule', 'updates'].map(name => `/servers/1/${name}`),
  '/plugins', '/plugins/1', '/settings', '/settings/discord', '/settings/profile', '/audit',
  '/assistant?conversation=conversation-1', '/live-console/1',
];
export const CACHE_COMPONENTS_ACTORS = ['admin-a', 'member-a', 'admin-b', 'member-b'];
export const CACHE_COMPONENTS_LOCALES = ['en-US', 'zh-CN'];
export const CACHE_COMPONENTS_WIDTHS = [390, 1440];
export const FIXTURE_STAMP = '2026-10-08T00:00:00Z';
export const FIXTURE_OPERATION = '00000000-0000-4000-8000-000000000001';

export function fixtureServerIds(actor) {
  const tenantOffset = actor.endsWith('-a') ? 1 : 2;
  return Array.from({ length: 250 }, (_, index) => index * 2 + tenantOffset);
}

export function surfaceForServer(surface, id) {
  return surface.replace(/^\/servers\/1(?=\/|$)/, `/servers/${id}`)
    .replace(/^\/live-console\/1$/, `/live-console/${id}`);
}
