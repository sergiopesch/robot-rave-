const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'robot_frontend.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function frontend() {
  const elements = new Map();
  const requests = [];
  let status = 200;
  const context = vm.createContext({
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: '', textContent: '' });
      return elements.get(id);
    } },
    window: { addEventListener() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { status, ok: status === 200, json: async () => ({}) };
    },
  });
  vm.runInContext(script, context);
  return { context, elements, requests, rejectToken() { status = 401; } };
}

test('requests stay disconnected until token entry; authorized requests use headers', async () => {
  const ui = frontend();
  await assert.rejects(ui.context.apiRequest('/api/control/forward'), /Connect/);
  await ui.context.fetchData();
  assert.equal(ui.requests.length, 0);
  const input = ui.context.document.getElementById('api-token');
  input.value = 'fixture-token-not-a-real-secret';
  await ui.context.connectApi();
  assert.equal(input.value, '');
  assert.equal(ui.requests[0].url, '/api/status');
  assert.equal(ui.requests[0].options.headers.Authorization, 'Bearer fixture-token-not-a-real-secret');
  await ui.context.apiRequest('/api/control/stop', { method: 'POST' });
  assert.equal(ui.requests[1].url, '/api/control/stop');
  assert.equal(ui.requests[1].options.method, 'POST');
  assert.equal(ui.requests[1].options.headers.Authorization, 'Bearer fixture-token-not-a-real-secret');
});

test('a rejected credential is cleared, and no subsequent control request is sent', async () => {
  const ui = frontend();
  ui.context.document.getElementById('api-token').value = 'rejected-fixture-token';
  ui.rejectToken();
  await ui.context.connectApi();
  assert.match(ui.context.document.getElementById('api-auth-state').textContent, /Token rejected/);
  assert.equal(ui.requests.length, 1);
  await assert.rejects(ui.context.apiRequest('/api/control/forward'), /Connect/);
  assert.equal(ui.requests.length, 1);
});
