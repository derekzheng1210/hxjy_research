'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(
  require('path').join(__dirname, 'static', 'institution_flow', 'dashboard.js'),
  'utf8'
);
const start = source.indexOf('function createRequestGate()');
// 文件可能以 CRLF 检出（git autocrlf），双换行匹配需兼容 \r\n
const end = start + source.slice(start).search(/\r?\n\r?\nvar kpiRequests/);
assert(start >= 0 && end > start, 'request gate source not found');

const sandbox = {
  AbortController,
  setTimeout,
  clearTimeout
};
vm.runInNewContext(
  source.slice(start, end) + '\nthis.createRequestGate = createRequestGate;',
  sandbox,
  { filename: 'dashboard.js' }
);

function delayedCommit(gate, token, value, delay, commits) {
  return new Promise(function (resolve) {
    setTimeout(function () {
      if (gate.isCurrent(token.id)) commits.push(value);
      resolve();
    }, delay);
  });
}

async function main() {
  const gate = sandbox.createRequestGate();
  const oldRequest = gate.begin();
  const commits = [];
  const oldResponse = delayedCommit(gate, oldRequest, 'all-institutions', 30, commits);
  const newRequest = gate.begin();
  const newResponse = delayedCommit(gate, newRequest, 'fund', 5, commits);

  await Promise.all([oldResponse, newResponse]);
  assert.deepStrictEqual(commits, ['fund']);
  assert.strictEqual(oldRequest.signal.aborted, true);
  assert.strictEqual(gate.isCurrent(newRequest.id), true);

  gate.invalidate();
  assert.strictEqual(gate.isCurrent(newRequest.id), false);
  process.stdout.write('institution-flow request gate regression: PASS\n');
}

main().catch(function (err) {
  process.stderr.write(String(err.stack || err) + '\n');
  process.exitCode = 1;
});
