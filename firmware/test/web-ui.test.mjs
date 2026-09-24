import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../src/voice_config_httpd.c', import.meta.url), 'utf8');
const htmlSource = source.match(/static const char k_html\[\] =([\s\S]*?);\n\nstatic esp_err_t send_json/);
assert.ok(htmlSource, 'embedded setup page exists');
const html = [...htmlSource[1].matchAll(/"(?:\\.|[^"\\])*"/g)]
  .map(([literal]) => JSON.parse(literal)).join('');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'setup page has a script');

async function openSetupPage() {
  const elements = Object.fromEntries(
    ['scan', 'ssid', 'pass', 'url', 'token', 'protocol', 'ip', 'status', 'info', 'gateway-help', 'token-label'].map(id => [id, { value: '', textContent: '' }]),
  );
  const select = elements.scan;
  select.options = [];
  select.replaceChildren = (...options) => { select.options = options; };
  select.add = option => { select.options.push(option); };
  const requested = [];
  const intervals = [];
  const context = {
    document: { getElementById: id => elements[id] },
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    URLSearchParams,
    URL,
    fetch: async (path, options) => {
      requested.push({ path, options });
      return { json: async () => path === '/config'
        ? { wifi_ssid: '', gateway_url: '', device_id: '', protocol_version: 1, device_token_set: false, ip: '0.0.0.0', ap_ip: '192.168.4.1' }
        : { ok: true, scanning: false, networks: [{ ssid: 'Atitlan', rssi: -52 }] } };
    },
    setInterval: callback => { intervals.push(callback); },
    setTimeout: () => {},
  };
  runInNewContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  return { elements, intervals, requested, context };
}

test('setup page fills Wi-Fi choices on open without pressing Scan', async () => {
  const { elements, requested } = await openSetupPage();
  const select = elements.scan;
  assert.ok(requested.some(request => request.path === '/wifi_scan'), 'page requests cached scan results automatically');
  assert.equal(select.options.find(option => option.value === 'Atitlan')?.text, 'Atitlan (-52 dBm)');
});

test('selected network and edited fields survive periodic status updates', async () => {
  const { elements, intervals } = await openSetupPage();
  elements.scan.value = 'Atitlan';
  elements.scan.onchange();
  elements.url.value = 'http://gateway.local/api/v1/voice/turn';
  for (const refresh of intervals) await refresh();
  assert.equal(elements.ssid.value, 'Atitlan');
  assert.equal(elements.url.value, 'http://gateway.local/api/v1/voice/turn');
});

test('save explains missing required settings without posting secrets', async () => {
  const { elements, requested, context } = await openSetupPage();
  elements.scan.value = 'Atitlan';
  elements.scan.onchange();
  elements.pass.value = 'test-secret';
  await context.saveCfg();
  assert.match(elements.info.textContent, /Gateway endpoint/i);
  assert.equal(requested.some(request => request.options?.method === 'POST'), false);
});

test('setup page saves without an editable Device ID', async () => {
  const { elements, requested, context } = await openSetupPage();
  elements.scan.value = 'Atitlan';
  elements.scan.onchange();
  elements.url.value = 'http://gateway.local/api/v1/voice/turn';
  await context.saveCfg();
  const post = requested.find(request => request.options?.method === 'POST');
  assert.ok(post, 'settings are submitted without a Device ID field');
  assert.equal(post.options.body.has('device_id'), false);
});

test('setup defaults to protocol v1 and explains the endpoint format', async () => {
  const { elements } = await openSetupPage();
  assert.equal(elements.protocol.value, '1');
  assert.match(elements['gateway-help'].textContent, /api\/v1/i);
});

test('setup submits v2 only with a device token and an origin URL', async () => {
  const { elements, requested, context } = await openSetupPage();
  elements.scan.value = 'Atitlan';
  elements.scan.onchange();
  elements.url.value = 'http://gateway.local:8080';
  elements.protocol.value = '2';
  await context.saveCfg();
  assert.match(elements.info.textContent, /token/i);
  assert.equal(requested.some(request => request.options?.method === 'POST'), false);

  elements.token.value = 'device-token';
  await context.saveCfg();
  const post = requested.find(request => request.options?.method === 'POST');
  assert.ok(post);
  assert.equal(post.options.body.get('protocol_version'), '2');

  elements.url.value = 'http://gateway.local:8080/api/v1/voice/turn';
  await context.saveCfg();
  assert.match(elements.info.textContent, /base url/i);
});
