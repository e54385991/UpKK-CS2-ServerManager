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
