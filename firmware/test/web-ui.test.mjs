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

class Element {
  value = ''; textContent = ''; checked = false; hidden = false; children = [];
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  setAttribute(name,value) { this[name]=value; }
  focus() { this.focused=true; }
}
async function openSetupPage(config = {}, scan = {ok:true, networks:[{ssid:'Atitlan',rssi:-52}]}) {
  const elements = Object.fromEntries([...html.matchAll(/id='([^']+)'/g)].map(m=>[m[1],new Element()]));
  elements['wifi-editor'].hidden = true;
  const requested = [], intervals = [];
  const context = {
    document: {getElementById:id=>elements[id],createElement:()=>new Element()},
    URLSearchParams, URL, TextEncoder,
    fetch: async(path,options)=>{
      requested.push({path,options});
      return {ok:true,json:async()=>path==='/config'
        ? {wifi_ssid:'',gateway_url:'',device_token_set:true,ip:'0.0.0.0',ap_ip:'192.168.4.1',...config}
        : scan};
    },
    setInterval:callback=>intervals.push(callback),setTimeout:()=>{},
  };
  runInNewContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {elements,requested,intervals,context,scan};
}
function addOpenNetwork(page, ssid='test') {
  page.context.editWifi(ssid);
  page.elements['wifi-open'].checked=true;
  assert.equal(page.context.applyWifi(),true);
}

test('scanner loads automatically and shows a single hidden editor', async()=>{
  const {elements,requested,context}=await openSetupPage();
  assert.ok(requested.some(r=>r.path==='/wifi_scan'));
  assert.match(context.wifiRows()[0].status, /-52 дБм/);
  assert.equal(elements['wifi-editor'].hidden,true);
  assert.equal((html.match(/id='pass'/g)||[]).length,1);
  assert.equal(/id='(?:ssid[1-4]|pass[1-4]|wifi-slot)'/.test(html),false);
});
test('edits survive status updates and rescanning',async()=>{
  const {elements,context,intervals}=await openSetupPage();
  context.editWifi('Atitlan');elements.pass.value='test-password';elements.url.value='http://gateway.test';
  await context.scanWifi(true);
  for(const refresh of intervals) await refresh();
  assert.equal(elements.ssid.value,'Atitlan');assert.equal(elements.pass.value,'test-password');
  assert.equal(elements.url.value,'http://gateway.test');
});
test('save explains missing gateway without posting secrets',async()=>{
  const page=await openSetupPage();addOpenNetwork(page);
  await page.context.saveCfg();
  assert.match(page.elements.info.textContent,/адрес голосового сервера/);
  assert.equal(page.requested.some(r=>r.options?.method==='POST'),false);
});
test('setup saves without an editable device ID',async()=>{
  const page=await openSetupPage({gateway_url_set:true});addOpenNetwork(page);
  await page.context.saveCfg();
  const post=page.requested.find(r=>r.options?.method==='POST');assert.ok(post);
  assert.equal(post.options.body.has('device_id'),false);
});

test('setup exposes one protocol and a base URL without a version selector', async () => {
  const { elements } = await openSetupPage();
  assert.equal(elements.protocol, undefined);
  assert.doesNotMatch(html, /Voice protocol|synchronous|asynchronous/);
  assert.match(elements['gateway-help'].textContent, /базовый адрес/i);
});

test('setup submits only with a device token and an origin URL', async () => {
  const { elements, requested, context } = await openSetupPage({device_token_set:false});
  context.editWifi('Atitlan');
  elements['wifi-open'].checked = true;
  context.applyWifi();
  elements.url.value = 'http://gateway.local:8080';
  await context.saveCfg();
  assert.match(elements.info.textContent, /токен/i);
  assert.equal(requested.some(request => request.options?.method === 'POST'), false);

  elements.token.value = 'device-token';
  elements.url.value = 'http://gateway.local:8080/api/v1/voice/turn';
  await context.saveCfg();
  assert.match(elements.info.textContent, /базовый адрес/i);
  elements.url.value = 'http://gateway.local:8080';
  await context.saveCfg();
  const post = requested.find(request => request.options?.method === 'POST');
  assert.ok(post);
  assert.equal(post.options.body.has('protocol_version'), false);

});

test('sleep timeout loads a saved value and is submitted in seconds', async () => {
  const { elements, requested, context } = await openSetupPage({ sleep_timeout_seconds: 120 });
  assert.equal(elements['sleep-seconds'].value, '120');
  context.editWifi('test');
  elements['wifi-open'].checked = true;
  context.applyWifi();
  elements.url.value = 'http://gateway.local';
  elements.token.value = 'new-token';
  elements['sleep-seconds'].value = '45';
  await context.saveCfg();
  assert.equal(requested.find(r => r.options?.method === 'POST').options.body.get('sleep_timeout_seconds'), '45');
});
test('sleep timeout defaults to 30 and rejects invalid values without posting', async () => {
  const { elements, requested, context } = await openSetupPage();
  assert.equal(elements['sleep-seconds'].value, '30');
  context.editWifi('test');
  elements['wifi-open'].checked = true;
  context.applyWifi();
  elements.url.value = 'http://gateway.local';
  elements.token.value = 'new-token';
  for (const value of ['', '0', '4', '3601', '30s', '1.5', '-30']) {
    elements['sleep-seconds'].value = value;
    await context.saveCfg();
    assert.match(elements.info.textContent, /Время до сна/);
  }
  assert.equal(requested.some(r => r.options?.method === 'POST'), false);
});

test('WireGuard uses saved flags without filling inputs', async () => {
  const { elements, context, requested, intervals } = await openSetupPage({
    wg_enabled: true, wg_address: '10.7.0.2', wg_endpoint: 'vpn.example.com',
    wg_port: 51821, wg_keepalive: 30, wg_private_key_set: true,
    wg_preshared_key_set: true, wg_status: 'connected',
  });
  assert.equal(elements['wg-enabled'].checked, true);
  assert.equal(elements['wg-full_tunnel'], undefined);
  assert.equal(elements['wg-port'].value, '');
  assert.equal(elements['wg-private_key'].value, '');
  assert.equal(elements['wg-preshared_key'].value, '');
  assert.match(elements['wg-status'].textContent, /подключён/);
  elements['wg-endpoint'].value = 'edited.example.com';
  for (const refresh of intervals) await refresh();
  assert.equal(elements['wg-endpoint'].value, 'edited.example.com');
  context.editWifi('test');
  elements['wifi-open'].checked = true;
  context.applyWifi();
  elements.url.value = 'http://10.7.0.1:8080';
  elements.token.value = 'new-token';
  elements['wg-clear-psk'].checked = true;
  await context.saveCfg();
  const body = requested.find(r => r.options?.method === 'POST').options.body;
  assert.equal(body.get('wg_enabled'), '1');
  assert.equal(body.get('wg_endpoint'), 'edited.example.com');
  assert.equal(body.has('wg_private_key'), false);
  assert.equal(body.get('wg_clear_psk'), '1');
});

test('saved networks get checkmarks and absent networks stay visible',async()=>{
  const page=await openSetupPage({wifi_networks:[{ssid:'Atitlan',password_set:true},{ssid:'Work',password_set:true}]});
  const rows=page.context.wifiRows();
  assert.equal(rows.find(n=>n.ssid==='Atitlan').saved,true);
  assert.match(rows.find(n=>n.ssid==='Atitlan').status,/-52 дБм/);
  assert.equal(rows.find(n=>n.ssid==='Work').saved,true);
  assert.match(rows.find(n=>n.ssid==='Work').status,/Не видна/);
  assert.match(page.elements['wifi-list'].children[1].children[0].textContent,/✓ Work/);
});
test('failed scan never labels saved networks as invisible',async()=>{
  const {context}=await openSetupPage({wifi_networks:[{ssid:'Work',password_set:true}]},{ok:false,scanning:false});
  assert.match(context.wifiRows()[0].status,/Видимость неизвестна/);
  assert.doesNotMatch(context.wifiRows()[0].status,/Не видна/);
});
test('duplicate access points collapse to strongest signal without rendering SSID HTML',async()=>{
  const ssid='<img src=x onerror=alert(1)>';
  const {context,elements}=await openSetupPage({}, {ok:true,networks:[{ssid,rssi:-80},{ssid,rssi:-30}]});
  assert.equal(context.wifiRows().length,1);
  assert.match(context.wifiRows()[0].status,/-30 дБм/);
  assert.equal(elements['wifi-list'].children[0].children[0].textContent,ssid);
});
test('delete and add preserve other slot credentials and do not claim pending data is saved',async()=>{
  const page=await openSetupPage({gateway_url_set:true,wifi_networks:[
    {ssid:'Home',password_set:true},{ssid:'Work',password_set:true},{ssid:'Cottage',password_set:true}]});
  page.context.editWifi('Work');assert.equal(page.elements.pass.value,'');page.context.removeWifi();
  page.context.editWifi('Phone');page.elements.pass.value='phone-password';assert.equal(page.context.applyWifi(),true);
  assert.equal(page.context.wifiRows().find(n=>n.ssid==='Phone').saved,false);
  await page.context.saveCfg();const body=page.requested.find(r=>r.options?.method==='POST').options.body;
  assert.equal(body.get('wifi1_ssid'),'Phone');assert.equal(body.get('wifi1_password'),'phone-password');
  assert.equal(body.get('wifi2_ssid'),'Cottage');assert.equal(body.get('wifi2_password'),'');
});
test('saved network keeps blank password, rename requires a new one, cancel discards edits',async()=>{
  const page=await openSetupPage({gateway_url_set:true,wifi_networks:[{ssid:'Home',password_set:true}]});
  page.context.editWifi('Home');assert.equal(page.elements.pass.value,'');assert.equal(page.context.applyWifi(),true);
  page.context.editWifi('Home');page.elements.ssid.value='NewHome';assert.equal(page.context.applyWifi(),false);
  assert.match(page.elements['wifi-error'].textContent,/пароль/);page.context.closeWifi();
  assert.equal(page.context.wifiRows()[0].ssid,'Home');
  await page.context.saveCfg();assert.equal(page.requested.find(r=>r.options?.method==='POST').options.body.get('wifi0_password'),'');
});
test('full list, duplicate names, and no remaining networks are explained',async()=>{
  const page=await openSetupPage({gateway_url_set:true,wifi_networks:
    Array.from({length:5},(_,i)=>({ssid:'Net'+i,password_set:true}))});
  page.context.editWifi('Extra');page.elements.pass.value='valid-password';assert.equal(page.context.applyWifi(),false);
  assert.match(page.elements['wifi-error'].textContent,/5 сетей/);
  page.context.editWifi('Net0');page.elements.ssid.value='Net1';assert.equal(page.context.applyWifi(),false);
  assert.match(page.elements['wifi-error'].textContent,/уже есть/);
  for(let i=0;i<5;i++){page.context.editWifi('Net'+i);page.context.removeWifi();}
  await page.context.saveCfg();assert.match(page.elements.info.textContent,/хотя бы одну/);
  assert.equal(page.requested.some(r=>r.options?.method==='POST'),false);
});
test('saved network reappearing after a scan clears the not-visible indication',async()=>{
  const page=await openSetupPage({wifi_networks:[{ssid:'Work',password_set:true}]});
  assert.match(page.context.wifiRows()[0].status,/Не видна/);
  page.scan.networks.push({ssid:'Work',rssi:-48});await page.context.scanWifi(true);
  assert.match(page.context.wifiRows()[0].status,/-48 дБм/);assert.equal(page.context.wifiRows()[0].saved,true);
});

test('reset cancellation never sends a request', async()=>{
  const page=await openSetupPage();
  page.context.confirm=message=>{assert.match(message,/ВСЕ настройки/);return false;};
  await page.context.resetCfg();
  assert.equal(page.requested.some(r=>r.path==='/config/reset'),false);
});
test('confirmed reset posts once and blocks repeated clicks during restart', async()=>{
  const page=await openSetupPage();
  page.context.confirm=()=>true;
  await Promise.all([page.context.resetCfg(),page.context.resetCfg()]);
  const resets=page.requested.filter(r=>r.path==='/config/reset');
  assert.equal(resets.length,1);
  assert.equal(resets[0].options.method,'POST');
  assert.equal(resets[0].options.headers['X-Hermes-Reset'],'confirm');
  assert.equal(page.elements['reset-settings'].disabled,true);
  assert.match(page.elements['reset-status'].textContent,/Подключитесь/);
});
test('failed reset can be retried only with another confirmation', async()=>{
  for(const networkError of [false,true]){
    const page=await openSetupPage();let confirmations=0;
    page.context.confirm=()=>{confirmations++;return true;};
    page.context.fetch=async()=>{if(networkError)throw Error('offline');return {ok:false};};
    await page.context.resetCfg();
    assert.equal(page.elements['reset-settings'].disabled,false);
    assert.match(page.elements['reset-status'].textContent,/Не удалось/);
    await page.context.resetCfg();assert.equal(confirmations,2);
  }
});

test('three tabs switch panels without discarding edits', async()=>{
  const page=await openSetupPage();
  assert.match(html, /role='tablist'/);
  for(const name of ['wifi','server','vpn']) assert.ok(page.elements['tab-'+name]);
  page.elements.url.value='http://edited.test';
  page.context.selectTab('vpn');
  assert.equal(page.elements['panel-wifi'].hidden,true);
  assert.equal(page.elements['panel-server'].hidden,true);
  assert.equal(page.elements['panel-vpn'].hidden,false);
  page.context.selectTab('server');
  assert.equal(page.elements.url.value,'http://edited.test');
});

test('saved server and VPN values never populate inputs even from a legacy response',async()=>{
  const page=await openSetupPage({gateway_url_set:true,gateway_url:'http://private.test',
    wg_address:'10.7.0.2',wg_endpoint:'secret.test',wg_port:51821,wg_keepalive:44,
    wg_public_key:'secret-key',wg_ntp_server:'private-ntp.test'});
  for(const id of ['url','token','wg-address','wg-netmask','wg-endpoint','wg-port',
                    'wg-keepalive','wg-public_key','wg-private_key','wg-preshared_key','wg-ntp_server'])
    assert.equal(page.elements[id].value,'',id);
});

test('unchanged hidden settings can be saved without resending them',async()=>{
  const page=await openSetupPage({gateway_url_set:true,wifi_networks:[{ssid:'Home',password_set:true}]});
  await page.context.saveCfg();
  const post=page.requested.find(r=>r.options?.method==='POST');
  assert.ok(post);
  for(const key of ['gateway_url','wg_address','wg_netmask','wg_endpoint','wg_port','wg_public_key','wg_keepalive','wg_ntp_server'])
    assert.equal(post.options.body.has(key),false,key);
  assert.equal(post.options.headers['X-Hermes-Setup'],'1');
});

test('changing server requires entering a token instead of reusing the stored one',async()=>{
  const page=await openSetupPage({gateway_url_set:true,wifi_networks:[{ssid:'Home',password_set:true}]});
  page.elements.url.value='http://another.test';
  await page.context.saveCfg();
  assert.equal(page.requested.some(r=>r.options?.method==='POST'),false);
  assert.match(page.elements.info.textContent,/токен/i);
});
