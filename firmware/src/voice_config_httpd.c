#include "voice_config_httpd.h"
#include "voice_mdns.h"

#ifdef ESP_PLATFORM
#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <lwip/sockets.h>

#include "esp_http_server.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "voice_captive_dns.h"
#include "voice_setup_access.h"

static const char *TAG = "voice_httpd";
static httpd_handle_t s_server;
static voice_settings_t *s_settings;
static char s_scan_json[2048] = "{\"ok\":false,\"scanning\":true,\"networks\":[]}";
static bool s_scan_ready;
static _Atomic bool s_scan_running;
static SemaphoreHandle_t s_scan_mutex;
static wifi_ap_record_t s_scan_records[24];
static char s_scan_body[sizeof(s_scan_json)];

/* Serve setup on the device AP and the currently connected Wi-Fi subnet. */
static bool allow_setup_client(httpd_req_t *req) {
  struct sockaddr_storage peer = {0};
  socklen_t peer_len = sizeof(peer);
  int fd = httpd_req_to_sockfd(req);
  bool allowed = false;
  if (fd >= 0 && getpeername(fd, (struct sockaddr *)&peer, &peer_len) == 0) {
    const uint8_t *address = NULL;
    if (peer.ss_family == AF_INET)
      address = (const uint8_t *)&((const struct sockaddr_in *)&peer)->sin_addr;
    else if (peer.ss_family == AF_INET6)
      address = ((const struct sockaddr_in6 *)&peer)->sin6_addr.s6_addr;
    const char *interfaces[] = {"WIFI_AP_DEF", "WIFI_STA_DEF"};
    for (size_t i = 0; i < 2 && !allowed; ++i) {
      esp_netif_t *netif = esp_netif_get_handle_from_ifkey(interfaces[i]);
      esp_netif_ip_info_t ip = {0};
      if (netif && esp_netif_is_netif_up(netif) &&
          esp_netif_get_ip_info(netif, &ip) == ESP_OK && ip.ip.addr && ip.netmask.addr)
        allowed = voice_setup_ipv4_allowed(peer.ss_family, address,
                                           ntohl(ip.ip.addr), ntohl(ip.netmask.addr));
    }
  }
  if (!allowed) {
    httpd_resp_set_status(req, "403 Forbidden");
    httpd_resp_send(req, "Доступ разрешён только из локальной Wi-Fi сети или сети настройки", HTTPD_RESP_USE_STRLEN);
  }
  return allowed;
}

static const char k_html[] =
  "<!doctype html>\n"
  "<html lang='ru'>\n"
  "<head>\n"
  "<meta charset='utf-8'>\n"
  "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
  "<title>Hermes StickS3</title>\n"
  "<style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:10px}h1{font-size:18px;margin:0 0 10px}.card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px;margin-bottom:10px}label{display:block;font-size:12px;color:#9ca3af;margin:9px 0 3px}input{width:100%;background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:9px;font-size:14px}button{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:10px 14px;font-size:14px;cursor:pointer}.save{background:#065f46;border-color:#047857;width:100%;margin-top:12px}.row{display:flex;gap:8px;align-items:center}.muted{opacity:.7;font-size:12px}select{flex:1;background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:9px}#wifi-list{display:grid;gap:6px;max-height:300px;overflow:auto;margin:10px 0}.wifi-item{text-align:left;display:flex;flex-direction:column;gap:4px;min-width:0}.wifi-name{overflow-wrap:anywhere}fieldset{min-width:0;border:1px solid #334155;border-radius:8px;margin:12px 0;padding:12px}#wifi-editor{border-top:1px solid #334155;margin-top:12px;padding-top:8px}.row{flex-wrap:wrap}#wifi-error{color:#fca5a5}[hidden]{display:none!important}.tabs{display:flex;gap:4px;margin-bottom:12px}.tabs button{flex:1;padding:10px 4px;font-size:13px}.tabs [aria-selected=true]{background:#065f46;border-color:#10b981}button:focus-visible{outline:2px solid #6ee7b7;outline-offset:2px}</style>\n"
  "</head>\n"
  "<body>\n"
  "<h1>🤖 Hermes StickS3 <span class='muted' id='ip'>\n"
  "</span>\n"
  "</h1>\n"
  "<div class='card'>\n"
  "<div class='tabs' role='tablist' aria-label='Раздел настроек'>\n"
  "<button type='button' id='tab-wifi' role='tab' aria-controls='panel-wifi' aria-selected='true' tabindex='0' onclick=\"selectTab('wifi')\" onkeydown=\"tabKey(event,'wifi')\">Wi-Fi</button>\n"
  "<button type='button' id='tab-server' role='tab' aria-controls='panel-server' aria-selected='false' tabindex='-1' onclick=\"selectTab('server')\" onkeydown=\"tabKey(event,'server')\">Настройки сервера</button>\n"
  "<button type='button' id='tab-vpn' role='tab' aria-controls='panel-vpn' aria-selected='false' tabindex='-1' onclick=\"selectTab('vpn')\" onkeydown=\"tabKey(event,'vpn')\">VPN</button>\n"
  "</div>\n"
  "<p class='muted'>Сохранённые адреса, ключи, токены и пароли не передаются в браузер. Пустые поля оставляют прежние значения.</p>\n"
  "<section id='panel-wifi' role='tabpanel' aria-labelledby='tab-wifi'>\n"
  "<fieldset>\n"
  "<legend>Wi-Fi сети</legend>\n"
  "<div class='row'>\n"
  "<button type='button' onclick='scanWifi(true)'>Сканировать</button>\n"
  "<button type='button' onclick='editWifi()'>Добавить вручную</button>\n"
  "</div>\n"
  "<div id='wifi-scan-status' class='muted' aria-live='polite'>\n"
  "</div>\n"
  "<div id='wifi-list' aria-label='Найденные и сохранённые сети'>\n"
  "</div>\n"
  "<div class='muted'>✓ — сохранена на диктофоне. «Не видна» — не найдена последним сканированием. Изменения применятся после Сохранить и перезагрузить.</div>\n"
  "<div id='wifi-editor' hidden>\n"
  "<label for='ssid'>Имя сети (SSID)</label>\n"
  "<input id='ssid' autocomplete='off'>\n"
  "<label for='pass'>Пароль</label>\n"
  "<input id='pass' type='password' autocomplete='new-password'>\n"
  "<label>\n"
  "<input id='wifi-open' type='checkbox' style='width:auto'> Без пароля (открытая сеть)</label>\n"
  "<div class='row'>\n"
  "<button type='button' onclick='applyWifi()'>Применить</button>\n"
  "<button id='wifi-delete' type='button' onclick='removeWifi()'>Удалить сеть</button>\n"
  "<button type='button' onclick='closeWifi()'>Отмена</button>\n"
  "</div>\n"
  "<div id='wifi-error' class='muted' role='alert'>\n"
  "</div>\n"
  "</div>\n"
  "</fieldset>\n"
  "</section>\n"
  "<section id='panel-server' role='tabpanel' aria-labelledby='tab-server' hidden>\n"
  "<label for='url'>Адрес голосового сервера</label>\n"
  "<input id='url' placeholder='http://192.168.1.10:8080'>\n"
  "<div class='muted' id='gateway-help'>Только базовый адрес, например http://192.168.1.10:8080</div>\n"
  "<label id='token-label'>Токен устройства (обязательно)</label>\n"
  "<input id='token' type='password' placeholder='Пусто — оставить сохранённый токен'>\n"
  "<label for='sleep-seconds'>Переход в сон от батареи (секунды)</label>\n"
  "<input id='sleep-seconds' type='number' min='5' max='3600' step='1' value='30'>\n"
  "<div class='muted'>От 5 до 3600 секунд бездействия. При питании от USB автоматический сон отключён.</div>\n"
  "</section>\n"
  "<section id='panel-vpn' role='tabpanel' aria-labelledby='tab-vpn' hidden>\n"
  "<fieldset>\n"
  "<legend>WireGuard VPN</legend>\n"
  "<label>\n"
  "<input id='wg-enabled' type='checkbox' style='width:auto'> Включить WireGuard</label>\n"
  "<label for='wg-address'>IPv4-адрес диктофона внутри VPN</label>\n"
  "<input id='wg-address' type='text' placeholder='10.7.0.2' autocomplete='off'>\n"
  "<label for='wg-netmask'>Маска подсети VPN</label>\n"
  "<input id='wg-netmask' type='text' placeholder='255.255.255.0' autocomplete='off'>\n"
  "<label for='wg-endpoint'>Имя или IPv4-адрес сервера WireGuard</label>\n"
  "<input id='wg-endpoint' type='text' placeholder='vpn.example.com' autocomplete='off'>\n"
  "<label for='wg-port'>UDP-порт сервера</label>\n"
  "<input id='wg-port' type='number' placeholder='51820' autocomplete='off'>\n"
  "<label for='wg-public_key'>Публичный ключ сервера</label>\n"
  "<input id='wg-public_key' type='text' placeholder='' autocomplete='off'>\n"
  "<label for='wg-private_key'>Приватный ключ диктофона</label>\n"
  "<input id='wg-private_key' type='password' placeholder='Пусто — оставить сохранённый ключ' autocomplete='off'>\n"
  "<label for='wg-preshared_key'>Предварительно согласованный ключ (необязательно)</label>\n"
  "<input id='wg-preshared_key' type='password' placeholder='Пусто — оставить сохранённый ключ' autocomplete='off'>\n"
  "<label for='wg-keepalive'>Интервал поддержания соединения (секунды; 0 — отключить)</label>\n"
  "<input id='wg-keepalive' type='number' placeholder='25' autocomplete='off'>\n"
  "<label for='wg-ntp_server'>Сервер времени NTP, доступный без VPN</label>\n"
  "<input id='wg-ntp_server' type='text' placeholder='pool.ntp.org' autocomplete='off'>\n"
  "<label>\n"
  "<input id='wg-clear-psk' type='checkbox' style='width:auto'> Удалить сохранённый предварительно согласованный ключ</label>\n"
  "<div class='muted'>При включённом WireGuard голосовой сервер доступен через VPN, в том числе по адресу в домашней сети. VPN также используется для выхода в интернет. Сервер WireGuard должен разрешать доступ к нужной сети. Настройки диктофона остаются доступны по Wi-Fi. Пустые поля сохраняют прежние значения. <span id='wg-keys'>\n"
  "</span>\n"
  "</div>\n"
  "<div class='muted' id='wg-status'>\n"
  "</div>\n"
  "</fieldset>\n"
  "</section>\n"
  "<button id='save-settings' class='save' onclick='saveCfg()'>Сохранить и перезагрузить</button>\n"
  "<div class='muted' id='info'>\n"
  "</div>\n"
  "</div>\n"
  "<div class='card'>\n"
  "<b>Сброс настроек</b>\n"
  "<p class='muted'>Удалить все сети Wi-Fi, пароли, настройки сервера и WireGuard. Таймер сна вернётся к 30 секундам.</p>\n"
  "<button id='reset-settings' style='color:#ff8a80' onclick='resetCfg()'>Сбросить все настройки</button>\n"
  "<div id='reset-status' class='muted' role='status'>\n"
  "</div>\n"
  "</div>\n"
  "<div class='card'>\n"
  "<b>Состояние</b>\n"
  "<div id='status' class='muted' style='margin-top:6px'>Загрузка…</div>\n"
  "</div>\n"
  "<script>const $=x=>document.getElementById(x);let savedToken=false,savedGateway=false;let savedWifi=[],wifiProfiles=[],visibleWifi=[],scanState='loading',editingWifi=-1,wifiLoaded=false;\n"
  "function selectTab(name){\n"
  "  for(const key of ['wifi','server','vpn']){\n"
  "    const selected=key===name;\n"
  "    $('panel-'+key).hidden=!selected;\n"
  "    $('tab-'+key).setAttribute('aria-selected',String(selected));\n"
  "    $('tab-'+key).tabIndex=selected?0:-1;\n"
  "  }\n"
  "}\n"
  "function tabKey(event,name){\n"
  "  const keys=['wifi','server','vpn'],index=keys.indexOf(name);\n"
  "  let next;\n"
  "  if(event.key==='ArrowRight')next=(index+1)%3;\n"
  "  else if(event.key==='ArrowLeft')next=(index+2)%3;\n"
  "  else if(event.key==='Home')next=0;\n"
  "  else if(event.key==='End')next=2;\n"
  "  else return;\n"
  "  event.preventDefault();selectTab(keys[next]);$('tab-'+keys[next]).focus();\n"
  "}\n"
  "function wgStatusText(status){return ({disabled:'выключен',paused_setup:'приостановлен на время настройки',waiting_wifi:'ожидание Wi-Fi',waiting_time:'синхронизация времени',connecting:'подключение…',connected:'подключён',error:'ошибка подключения',subnet_conflict:'конфликт подсетей'})[status]||'состояние неизвестно';}\n"
  "let resetPending=false;\n"
  "async function resetCfg(){\n"
  "  if(resetPending)return;\n"
  "  if(!confirm('Сбросить ВСЕ настройки диктофона?\\n\\nБудут удалены все сети Wi-Fi и пароли, адрес сервера, токен и ключи WireGuard. Таймер сна станет 30 секунд.\\n\\nДиктофон перезагрузится. Подключитесь к Hermes-StickS3-Setup и настройте его заново.'))return;\n"
  "  resetPending=true;$('reset-settings').disabled=true;$('reset-status').textContent='Сброс настроек…';\n"
  "  try{\n"
  "    const r=await fetch('/config/reset',{method:'POST',headers:{'X-Hermes-Reset':'confirm'}});\n"
  "    if(!r.ok)throw new Error('reset failed');\n"
  "    $('reset-status').textContent='Настройки сброшены. Перезагрузка… Подключитесь к сети Hermes-StickS3-Setup для настройки.';\n"
  "  }catch(e){\n"
  "    resetPending=false;$('reset-settings').disabled=false;\n"
  "    $('reset-status').textContent='Не удалось подтвердить сброс. Проверьте подключение к диктофону и повторите попытку.';\n"
  "  }\n"
  "}\n"
  "function wifiRows(){\n"
  "  const visible=new Map();\n"
  "  for(const n of visibleWifi){if(n.ssid&&(!visible.has(n.ssid)||n.rssi>visible.get(n.ssid).rssi))visible.set(n.ssid,n);}\n"
  "  const names=[...wifiProfiles.filter(n=>n.ssid).map(n=>n.ssid),...visible.keys()];\n"
  "  return [...new Set(names)].map(ssid=>{\n"
  "    const index=wifiProfiles.findIndex(n=>n.ssid===ssid),network=visible.get(ssid);\n"
  "    const saved=index>=0&&savedWifi[index]?.ssid===ssid;\n"
  "    let availability=scanState==='ready'?(network?network.rssi+' дБм':'Не видна'):scanState==='error'?'Видимость неизвестна':'Проверяем видимость…';\n"
  "    return {ssid,index,saved,status:[index>=0?(saved?'Сохранена':'Будет сохранена'):'Не сохранена',availability].join(' · ')};\n"
  "  });\n"
  "}\n"
  "function renderWifi(){\n"
  "  const list=$('wifi-list');list.replaceChildren();\n"
  "  for(const n of wifiRows()){\n"
  "    const button=document.createElement('button');button.type='button';button.className='wifi-item';\n"
  "    const name=document.createElement('span');name.className='wifi-name';name.textContent=(n.saved?'✓ ':'')+n.ssid;\n"
  "    const status=document.createElement('span');status.className='muted';status.textContent=n.status;\n"
  "    button.append(name,status);button.onclick=()=>editWifi(n.ssid);list.append(button);\n"
  "  }\n"
  "  $('wifi-scan-status').textContent=scanState==='loading'?'Сканирование…':scanState==='error'?'Не удалось просканировать сети. Повторите сканирование.':list.children.length?'':'Сети не найдены. Можно добавить скрытую сеть вручную.';\n"
  "}\n"
  "function editWifi(ssid=''){\n"
  "  if(!wifiLoaded)return;\n"
  "  editingWifi=wifiProfiles.findIndex(n=>n.ssid===ssid&&ssid);\n"
  "  const n=wifiProfiles[editingWifi];\n"
  "  $('ssid').value=n?n.ssid:ssid;$('pass').value=n?.password||'';\n"
  "  $('wifi-open').checked=!!n?.open;$('wifi-editor').hidden=false;\n"
  "  $('wifi-delete').hidden=editingWifi<0;\n"
  "  $('wifi-error').textContent='';\n"
  "  $('pass').placeholder=n&&savedWifi[editingWifi]?.ssid===ssid&&savedWifi[editingWifi]?.password_set?'Пусто — оставить сохранённый пароль':'Пароль сети';\n"
  "}\n"
  "function closeWifi(){\n"
  "  $('wifi-editor').hidden=true;$('ssid').value='';$('pass').value='';$('wifi-error').textContent='';editingWifi=-1;\n"
  "}\n"
  "function applyWifi(){\n"
  "  const ssid=$('ssid').value,password=$('pass').value,open=$('wifi-open').checked;\n"
  "  const error=message=>{$('wifi-error').textContent=message;return false;};\n"
  "  if(!ssid)return error('Введите имя сети (SSID)');\n"
  "  if(new TextEncoder().encode(ssid).length>32)return error('Имя сети: не более 32 байт UTF-8');\n"
  "  let index=editingWifi;\n"
  "  if(wifiProfiles.some((n,i)=>n.ssid===ssid&&i!==index))return error('Эта сеть уже есть в списке');\n"
  "  if(index<0)index=wifiProfiles.findIndex(n=>!n.ssid);\n"
  "  if(index<0)return error('Сохранено 5 сетей. Удалите ненужную перед добавлением новой.');\n"
  "  const retain=savedWifi[index]?.ssid===ssid&&savedWifi[index]?.password_set;\n"
  "  if(!open&&!password&&!retain)return error('Введите пароль или отметьте «Без пароля»');\n"
  "  const length=new TextEncoder().encode(password).length;\n"
  "  if(!open&&password&&!(length>=8&&length<=63||/^[0-9a-fA-F]{64}$/.test(password)))return error('Пароль: 8–63 байта или 64 шестнадцатеричных символа');\n"
  "  wifiProfiles[index]={ssid,password:open?'':password,open};\n"
  "  closeWifi();renderWifi();$('info').textContent='Изменения Wi-Fi ещё не сохранены. Нажмите «Сохранить и перезагрузить».';\n"
  "  return true;\n"
  "}\n"
  "function removeWifi(){\n"
  "  if(editingWifi<0)return;\n"
  "  wifiProfiles[editingWifi]={ssid:'',password:'',open:false};\n"
  "  closeWifi();renderWifi();$('info').textContent='Удаление применится после «Сохранить и перезагрузить».';\n"
  "}\n"
  "async function scanWifi(refresh=false){\n"
  "  scanState='loading';renderWifi();\n"
  "  try{\n"
  "    const j=await(await fetch(refresh?'/wifi_scan?refresh=1':'/wifi_scan')).json();\n"
  "    if(!j.ok&&j.scanning){setTimeout(()=>scanWifi(),1000);return;}\n"
  "    if(!j.ok)throw new Error('scan failed');\n"
  "    visibleWifi=j.networks||[];scanState='ready';renderWifi();\n"
  "  }catch(e){scanState='error';renderWifi();}\n"
  "}\n"
  "async function load(){\n"
  "  try{\n"
  "    const response=await fetch('/config',{cache:'no-store'});\n"
  "    if(!response.ok)throw new Error('config unavailable');\n"
  "    const j=await response.json();\n"
  "    savedWifi=j.wifi_networks||[];\n"
  "    wifiProfiles=Array.from({length:5},(_,i)=>({ssid:savedWifi[i]?.ssid||'',password:'',open:!!savedWifi[i]?.ssid&&!savedWifi[i]?.password_set}));\n"
  "    wifiLoaded=true;$('wifi-editor').hidden=true;renderWifi();\n"
  "    savedGateway=!!j.gateway_url_set;savedToken=!!j.device_token_set;\n"
  "    for(const [id,key] of [['url','gateway_url'],['token','device_token'],\n"
  "      ...['address','netmask','endpoint','port','public_key','private_key','preshared_key','keepalive','ntp_server'].map(n=>['wg-'+n,'wg_'+n])]){\n"
  "      $(id).value='';\n"
  "      if(j[key+'_set'])$(id).placeholder='Сохранено — пусто, чтобы оставить';\n"
  "    }\n"
  "    $('sleep-seconds').value=String(j.sleep_timeout_seconds??30);\n"
  "    $('wg-enabled').checked=!!j.wg_enabled;\n"
  "    $('wg-keys').textContent='Приватный ключ: '+(j.wg_private_key_set?'сохранён':'не задан')+'; общий ключ: '+(j.wg_preshared_key_set?'сохранён':'не задан');\n"
  "    $('wg-status').textContent='WireGuard: '+wgStatusText(j.wg_status);\n"
  "    $('gateway-help').textContent='Только базовый адрес. При его замене заново введите токен. Пусто — оставить сохранённый адрес.';\n"
  "    $('ip').textContent=j.ip&&j.ip!=='0.0.0.0'?'· '+j.ip:'';\n"
  "    $('status').textContent=j.wifi_connected?'Wi-Fi подключён · '+(j.active_ssid||'')+' · '+j.ip:'Точка доступа для настройки · '+j.ap_ip;\n"
  "  }catch(e){$('status').textContent='Не удалось получить состояние';}\n"
  "}\n"
  "let saving=false;\n"
  "async function saveCfg(){\n"
  "  if(saving)return;\n"
  "  const fail=(message,tab)=>{$('info').textContent=message;if(tab)selectTab(tab);};\n"
  "  if(!wifiLoaded)return fail('Дождитесь загрузки настроек');\n"
  "  const url=$('url').value.trim(),token=$('token').value;\n"
  "  if(!url&&!savedGateway)return fail('Введите адрес голосового сервера','server');\n"
  "  if(url){\n"
  "    if(!url.startsWith('http://')&&!url.startsWith('https://'))return fail('Адрес сервера должен начинаться с http:// или https://','server');\n"
  "    try{\n"
  "      const parsed=new URL(url);\n"
  "      if(parsed.pathname!=='/'||parsed.search||parsed.hash||parsed.username||parsed.password)\n"
  "        return fail('Введите базовый адрес сервера без пути и учётных данных','server');\n"
  "    }catch(e){return fail('Введите корректный базовый адрес сервера','server');}\n"
  "    if(!token)return fail('При вводе адреса сервера заново введите токен устройства','server');\n"
  "  }\n"
  "  if(!token&&!savedToken)return fail('Введите токен устройства','server');\n"
  "  if(!$('wifi-editor').hidden&&!applyWifi()){selectTab('wifi');return;}\n"
  "  const networks=wifiProfiles;\n"
  "  if(!networks.some(n=>n.ssid))return fail('Добавьте хотя бы одну Wi-Fi сеть','wifi');\n"
  "  const sleep=$('sleep-seconds').value;\n"
  "  if(!/^[0-9]+$/.test(sleep)||Number(sleep)<5||Number(sleep)>3600)\n"
  "    return fail('Время до сна должно быть целым числом от 5 до 3600 секунд','server');\n"
  "  const body=new URLSearchParams({sleep_timeout_seconds:sleep});\n"
  "  if(url)body.set('gateway_url',url);\n"
  "  if(token)body.set('device_token',token);\n"
  "  for(let i=0;i<5;i++){\n"
  "    body.set('wifi'+i+'_ssid',networks[i].ssid);\n"
  "    body.set('wifi'+i+'_password',networks[i].password);\n"
  "    body.set('wifi'+i+'_open',networks[i].open?'1':'0');\n"
  "  }\n"
  "  body.set('wg_enabled',$('wg-enabled').checked?'1':'0');\n"
  "  body.set('wg_clear_psk',$('wg-clear-psk').checked?'1':'0');\n"
  "  for(const name of ['address','netmask','endpoint','port','public_key','private_key','preshared_key','keepalive','ntp_server']){\n"
  "    const value=$('wg-'+name).value.trim();\n"
  "    if(value)body.set('wg_'+name,value);\n"
  "  }\n"
  "  saving=true;$('save-settings').disabled=true;$('info').textContent='Сохранение…';\n"
  "  try{\n"
  "    const r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-Hermes-Setup':'1'},body});\n"
  "    if(!r.ok)throw new Error('save rejected');\n"
  "    for(const id of ['url','token','pass',...['address','netmask','endpoint','port','public_key','private_key','preshared_key','keepalive','ntp_server'].map(n=>'wg-'+n)])$(id).value='';\n"
  "    for(const n of wifiProfiles)n.password='';\n"
  "    $('info').textContent='Сохранено. Перезагрузка…';\n"
  "  }catch(e){saving=false;$('save-settings').disabled=false;$('info').textContent='Не удалось сохранить настройки. Проверьте заполненные поля и повторите.';}\n"
  "}\n"
  "async function refreshWg(){try{const j=await(await fetch('/config')).json();$('wg-status').textContent='WireGuard: '+wgStatusText(j.wg_status);$('status').textContent=j.wifi_connected?'Wi‑Fi подключён · '+(j.active_ssid||'')+' · '+j.ip:'Точка доступа для настройки · '+j.ap_ip;}catch(e){}}load();scanWifi();setInterval(refreshWg,3000);</script>\n"
  "</body>\n"
  "</html>\n";

static esp_err_t send_json(httpd_req_t *req, const char *body) {
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
}

static bool json_string(char *out, size_t cap, size_t *used, const char *value) {
  if (*used + 2 >= cap) return false;
  out[(*used)++] = '"';
  for (const unsigned char *p = (const unsigned char *)(value ? value : ""); *p; ++p) {
    const char *escape = NULL;
    switch (*p) {
      case '"': escape = "\\\""; break;
      case '\\': escape = "\\\\"; break;
      case '\n': escape = "\\n"; break;
      case '\r': escape = "\\r"; break;
      case '\t': escape = "\\t"; break;
      default: break;
    }
    if (escape) {
      size_t n = strlen(escape);
      if (*used + n + 2 >= cap) return false;
      memcpy(out + *used, escape, n); *used += n;
    } else if (*p < 0x20) {
      if (*used + 6 + 2 >= cap) return false;
      int n = snprintf(out + *used, cap - *used, "\\u%04x", *p);
      if (n != 6) return false;
      *used += (size_t)n;
    } else {
      if (*used + 3 >= cap) return false;
      out[(*used)++] = (char)*p;
    }
  }
  out[(*used)++] = '"';
  out[*used] = '\0';
  return true;
}

static esp_err_t h_root(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, k_html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t h_portal_redirect(httpd_req_t *req, httpd_err_code_t error) {
  (void)error;
  if (!allow_setup_client(req)) return ESP_OK;
  httpd_resp_set_status(req, "302 Found");
  httpd_resp_set_hdr(req, "Location", "/");
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req,
      "<html lang='ru'><body><a href='/'>Открыть настройки Hermes StickS3</a></body></html>",
      HTTPD_RESP_USE_STRLEN);
}

static esp_err_t h_config_get(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  esp_netif_ip_info_t sta_ip = {0}, ap_ip = {0};
  esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
  esp_netif_t *ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
  if (sta) (void)esp_netif_get_ip_info(sta, &sta_ip);
  if (ap) (void)esp_netif_get_ip_info(ap, &ap_ip);
  char ip[16] = "0.0.0.0", ap_addr[16] = "192.168.4.1";
  if (sta_ip.ip.addr) snprintf(ip, sizeof(ip), "%u.%u.%u.%u", IP2STR(&sta_ip.ip));
  if (ap_ip.ip.addr) snprintf(ap_addr, sizeof(ap_addr), "%u.%u.%u.%u", IP2STR(&ap_ip.ip));
  char body[4096];
  if (!voice_config_public_status(s_settings, body, sizeof(body))) return ESP_FAIL;
  size_t used = strlen(body) - 1; // Extend the allowlisted metadata object.
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wifi_networks\":[");
  for (int i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i) {
    used += (size_t)snprintf(body + used, sizeof(body) - used, "%s{\"ssid\":", i ? "," : "");
    if (!json_string(body, sizeof(body), &used, s_settings->wifi[i].ssid)) return ESP_FAIL;
    used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"password_set\":%s}",
        s_settings->wifi[i].password[0] ? "true" : "false");
  }
  used += (size_t)snprintf(body + used, sizeof(body) - used, "],\"active_ssid\":");
  wifi_ap_record_t active = {0};
  (void)esp_wifi_sta_get_ap_info(&active);
  if (!json_string(body, sizeof(body), &used, (const char *)active.ssid)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used,
      ",\"wg_status\":\"%s\",\"ip\":\"%s\",\"ap_ip\":\"%s\",\"wifi_connected\":%s}",
      voice_wireguard_status(), ip, ap_addr, sta_ip.ip.addr ? "true" : "false");
  return send_json(req, body);
}

static esp_err_t h_config_post(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  char setup_header[4];
  // Cross-origin forms cannot supply this header; no CORS is enabled.
  if (httpd_req_get_hdr_value_str(req, "X-Hermes-Setup", setup_header, sizeof(setup_header)) != ESP_OK ||
      strcmp(setup_header, "1") != 0) {
    httpd_resp_set_status(req, "403 Forbidden");
    return send_json(req, "{\"ok\":false,\"error\":\"setup header required\"}");
  }
  if (req->content_len <= 0 || req->content_len >= 4096) return ESP_ERR_INVALID_SIZE;
  char body[4096]; int got = 0;
  while (got < req->content_len) { int n = httpd_req_recv(req, body + got, req->content_len - got); if (n <= 0) return ESP_FAIL; got += n; }
  body[got] = 0;
  voice_settings_t next = *s_settings;
  if (!voice_config_parse_form(body, &next)) {
    httpd_resp_set_status(req, "400 Bad Request");
    return send_json(req, "{\"ok\":false,\"error\":\"invalid settings\"}");
  }
  if (voice_settings_save(&next) != ESP_OK) {
    httpd_resp_set_status(req, "500 Internal Server Error");
    return send_json(req, "{\"ok\":false,\"error\":\"save failed\"}");
  }
  *s_settings = next;
  httpd_resp_set_status(req, "202 Accepted");
  send_json(req, "{\"ok\":true,\"restarting\":true}");
  vTaskDelay(pdMS_TO_TICKS(250));
  esp_restart();
  return ESP_OK;
}

static esp_err_t h_config_reset(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  char confirmation[8];
  // A custom header also prevents cross-origin form submissions from resetting.
  if (req->content_len != 0 ||
      httpd_req_get_hdr_value_str(req, "X-Hermes-Reset", confirmation, sizeof(confirmation)) != ESP_OK ||
      strcmp(confirmation, "confirm") != 0) {
    httpd_resp_set_status(req, "400 Bad Request");
    return send_json(req, "{\"ok\":false,\"error\":\"confirmation required\"}");
  }
  if (voice_settings_reset() != ESP_OK) {
    httpd_resp_set_status(req, "500 Internal Server Error");
    return send_json(req, "{\"ok\":false,\"error\":\"reset failed\"}");
  }
  httpd_resp_set_status(req, "202 Accepted");
  send_json(req, "{\"ok\":true,\"restarting\":true}");
  vTaskDelay(pdMS_TO_TICKS(250));
  esp_restart();
  return ESP_OK;
}

static esp_err_t h_wifi_scan(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  if (strstr(req->uri, "refresh=1")) voice_config_httpd_setup_ap_started();
  char body[sizeof(s_scan_json)];
  xSemaphoreTake(s_scan_mutex, portMAX_DELAY);
  bool ready = s_scan_ready;
  strlcpy(body, ready ? s_scan_json :
          "{\"ok\":false,\"scanning\":true,\"networks\":[]}", sizeof(body));
  xSemaphoreGive(s_scan_mutex);
  if (!ready) voice_config_httpd_setup_ap_started();
  ESP_LOGI(TAG, "wifi scan cache request: ready=%d bytes=%u", (int)ready,
           (unsigned)strlen(body));
  return send_json(req, body);
}

static void scan_done(void *arg, esp_event_base_t base, int32_t id, void *data) {
  (void)base; (void)id; (void)data;
  xTaskNotifyGive((TaskHandle_t)arg);
}

bool voice_config_httpd_wifi_scanning(void) { return s_scan_running; }

static void wifi_scan_task(void *arg) {
  (void)arg;
  vTaskDelay(pdMS_TO_TICKS(500));
  wifi_mode_t mode = WIFI_MODE_NULL;
  esp_err_t err = esp_wifi_get_mode(&mode);
  if (err == ESP_OK && mode != WIFI_MODE_APSTA && mode != WIFI_MODE_STA)
    err = ESP_ERR_INVALID_STATE;
  if (err == ESP_OK && mode == WIFI_MODE_APSTA) {
    esp_netif_t *ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
    esp_err_t dns_err = voice_captive_dns_start(ap);
    if (dns_err != ESP_OK)
      ESP_LOGE(TAG, "captive DNS start failed: %s", esp_err_to_name(dns_err));
  }
  esp_event_handler_instance_t handler = NULL;
  if (err == ESP_OK) err = esp_event_handler_instance_register(
      WIFI_EVENT, WIFI_EVENT_SCAN_DONE, scan_done, xTaskGetCurrentTaskHandle(), &handler);
  if (err == ESP_OK) {
    wifi_ap_record_t associated;
    if (esp_wifi_sta_get_ap_info(&associated) != ESP_OK) (void)esp_wifi_disconnect();
    wifi_scan_config_t cfg = {0};
    cfg.show_hidden = true;
    err = esp_wifi_scan_start(&cfg, false);
    if (err == ESP_OK && !ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(15000))) {
      (void)esp_wifi_scan_stop();
      err = ESP_ERR_TIMEOUT;
    }
  }
  if (handler) esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_SCAN_DONE, handler);
  uint16_t count = 24;
  if (err == ESP_OK) err = esp_wifi_scan_get_ap_records(&count, s_scan_records);
  char *body = s_scan_body;
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "startup Wi-Fi scan failed: %s", esp_err_to_name(err));
    snprintf(body, sizeof(s_scan_body), "{\"ok\":false,\"scanning\":false,\"networks\":[]}");
  } else {
    size_t used = (size_t)snprintf(body, sizeof(s_scan_body), "{\"ok\":true,\"scanning\":false,\"networks\":[");
    bool first = true;
    for (uint16_t i = 0; i < count; ++i) {
      if (!s_scan_records[i].ssid[0]) continue;
      char entry[256];
      size_t entry_used = (size_t)snprintf(entry, sizeof(entry), "%s{\"ssid\":", first ? "" : ",");
      if (!json_string(entry, sizeof(entry), &entry_used, (const char *)s_scan_records[i].ssid)) continue;
      int n = snprintf(entry + entry_used, sizeof(entry) - entry_used, ",\"rssi\":%d}", s_scan_records[i].rssi);
      if (n < 0 || (size_t)n >= sizeof(entry) - entry_used) continue;
      entry_used += (size_t)n;
      if (used + entry_used + 3 >= sizeof(s_scan_body)) break;
      memcpy(body + used, entry, entry_used);
      used += entry_used;
      first = false;
    }
    snprintf(body + used, sizeof(s_scan_body) - used, "]}");
    ESP_LOGI(TAG, "startup Wi-Fi scan complete: %u networks", (unsigned)count);
  }
  xSemaphoreTake(s_scan_mutex, portMAX_DELAY);
  strlcpy(s_scan_json, body, sizeof(s_scan_json));
  s_scan_ready = true;
  xSemaphoreGive(s_scan_mutex);
  s_scan_running = false;
  vTaskDelete(NULL);
}

void voice_config_httpd_setup_ap_started(void) {
  if (!s_server || atomic_exchange(&s_scan_running, true)) return;
  xSemaphoreTake(s_scan_mutex, portMAX_DELAY);
  s_scan_ready = false;
  xSemaphoreGive(s_scan_mutex);
  if (xTaskCreate(wifi_scan_task, "wifi_scan", 4096, NULL, 3, NULL) != pdPASS) {
    s_scan_running = false;
    xSemaphoreTake(s_scan_mutex, portMAX_DELAY);
    strcpy(s_scan_json, "{\"ok\":false,\"scanning\":false,\"networks\":[]}");
    s_scan_ready = true;
    xSemaphoreGive(s_scan_mutex);
    ESP_LOGE(TAG, "startup Wi-Fi scan task creation failed");
  }
}

void voice_config_httpd_start(voice_settings_t *settings) {
  if (s_server || !settings) return;
  s_scan_mutex = xSemaphoreCreateMutex();
  if (!s_scan_mutex) return;
  s_settings = settings;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.stack_size = 12288;
  if (httpd_start(&s_server, &cfg) != ESP_OK) { ESP_LOGE(TAG, "HTTP server start failed"); return; }
  static const httpd_uri_t root = {.uri = "/", .method = HTTP_GET, .handler = h_root};
  static const httpd_uri_t get_cfg = {.uri = "/config", .method = HTTP_GET, .handler = h_config_get};
  static const httpd_uri_t post_cfg = {.uri = "/config", .method = HTTP_POST, .handler = h_config_post};
  static const httpd_uri_t scan = {.uri = "/wifi_scan", .method = HTTP_GET, .handler = h_wifi_scan};
  static const httpd_uri_t reset_cfg = {.uri = "/config/reset", .method = HTTP_POST, .handler = h_config_reset};
  httpd_register_uri_handler(s_server, &reset_cfg);
  httpd_register_uri_handler(s_server, &root); httpd_register_uri_handler(s_server, &get_cfg);
  httpd_register_uri_handler(s_server, &post_cfg); httpd_register_uri_handler(s_server, &scan);
  httpd_register_err_handler(s_server, HTTPD_404_NOT_FOUND, h_portal_redirect);
  wifi_mode_t mode = WIFI_MODE_NULL;
  if (esp_wifi_get_mode(&mode) == ESP_OK && mode == WIFI_MODE_APSTA)
    voice_config_httpd_setup_ap_started();
  voice_mdns_start();
  ESP_LOGI(TAG, "settings UI started on port 80");
}
#else
void voice_config_httpd_start(voice_settings_t *settings) { (void)settings; }
void voice_config_httpd_setup_ap_started(void) {}
bool voice_config_httpd_wifi_scanning(void) { return false; }
#endif
