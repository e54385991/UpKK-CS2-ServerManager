import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../../frontend/src/modules/plugins/ai-import-tasks.tsx', import.meta.url),
  'utf8',
);

test('completed tab survives when the task list is empty', () => {
  assert.doesNotMatch(source, /if \(!tasks\.length\) return null;/);
  assert.match(source, /queueEmpty/);
  assert.match(source, /completedEmpty/);
});

test('activity tray renders import tasks when completed or failed tasks exist', () => {
  const tray = readFileSync(
    new URL('../../frontend/src/modules/shell/activity-tray.tsx', import.meta.url),
    'utf8',
  );
  const lists = readFileSync(
    new URL('../../frontend/src/modules/shell/activity-tray-lists.ts', import.meta.url),
    'utf8',
  );
  const panel = readFileSync(
    new URL('../../frontend/src/modules/shell/activity-tray-panel.tsx', import.meta.url),
    'utf8',
  );

  assert.match(tray, /hasVisibleMarketTasks/);
  assert.match(lists, /item\.status === "failed"/);
  assert.match(
    panel,
    /isAdmin && hasVisibleMarketTasks && <AIImportTasks initialTasks=\{marketTasks\} \/>/,
  );
});
