#include "voice_config_httpd.h"

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

/* The provisioning UI carries Wi-Fi credentials over plain HTTP. Keep it on
 * the device's setup subnet; do not expose it to the station/home LAN. */
static bool allow_setup_client(httpd_req_t *req) {
  struct sockaddr_storage peer = {0};
  socklen_t peer_len = sizeof(peer);
  int fd = httpd_req_to_sockfd(req);
  esp_netif_t *ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
  esp_netif_ip_info_t ap_ip = {0};
  bool allowed = false;
  if (ap) (void)esp_netif_get_ip_info(ap, &ap_ip);
  if (fd >= 0 && ap_ip.ip.addr && ap_ip.netmask.addr &&
      getpeername(fd, (struct sockaddr *)&peer, &peer_len) == 0) {
    const uint8_t *address = NULL;
    if (peer.ss_family == AF_INET) {
      address = (const uint8_t *)&((const struct sockaddr_in *)&peer)->sin_addr;
    } else if (peer.ss_family == AF_INET6) {
      address = ((const struct sockaddr_in6 *)&peer)->sin6_addr.s6_addr;
    }
    allowed = voice_setup_ipv4_allowed(peer.ss_family, address,
                                        ntohl(ap_ip.ip.addr), ntohl(ap_ip.netmask.addr));
  }
  if (!allowed) {
    httpd_resp_set_status(req, "403 Forbidden");
    httpd_resp_send(req, "setup network only", HTTPD_RESP_USE_STRLEN);
    return false;
  }
  return true;
}

static const char k_html[] =
  "<!doctype html>"
  "<html>"
  "<head>"
  "<meta charset='utf-8'>"
  "<meta name='viewport' content='width=device-width,initial-scale=1'>"
  "<title>Hermes StickS3</title>"
  "<style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:10px}h1{font-size:18px;margin:0 0 10px}.card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px;margin-bottom:10px}label{display:block;font-size:12px;color:#9ca3af;margin:9px 0 3px}input{width:100%;background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:9px;font-size:14px}button{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:10px 14px;font-size:14px;cursor:pointer}.save{background:#065f46;border-color:#047857;width:100%;margin-top:12px}.row{display:flex;gap:8px;align-items:center}.muted{opacity:.7;font-size:12px}select{flex:1;background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:9px}</style>"
  "</head>"
  "<body>"
  "<h1>🤖 Hermes StickS3 <span class='muted' id='ip'>"
  "</span>"
  "</h1>"
  "<div class='card'>"
  "<div class='muted'>Network and voice gateway</div>"
  "<label>Voice protocol</label>"
  "<select id='protocol' onchange='updateProtocol()'>"
  "<option value='1'>v1 · synchronous</option>"
  "<option value='2'>v2 · asynchronous</option>"
  "</select>"
  "<fieldset>"
  "<legend>Saved Wi-Fi networks</legend>"
  "<div class='muted'>Up to 5 networks, in priority order. Empty SSID removes a network. Blank password keeps the saved password only for the same SSID.</div>"
  "<label>Scan results</label>"
  "<div class='row'>"
  "<select id='scan'>"
  "<option value=''>— scan networks —</option>"
  "</select>"
  "<button type='button' onclick='scanWifi(true)'>Scan</button>"
  "</div>"
  "<label>Use selected network in slot</label>"
  "<select id='wifi-slot'>"
  "<option value='0'>Network 1</option>"
  "<option value='1'>Network 2</option>"
  "<option value='2'>Network 3</option>"
  "<option value='3'>Network 4</option>"
  "<option value='4'>Network 5</option>"
  "</select>"
  "<label for='ssid'>Network 1 SSID</label>"
  "<input id='ssid' maxlength='32' autocomplete='off'>"
  "<label for='pass'>Password</label>"
  "<input id='pass' type='password' maxlength='64' autocomplete='new-password' placeholder='blank keeps saved password'>"
  "<label>"
  "<input id='wifi-open0' type='checkbox' style='width:auto'> Open network (no password)</label>"
  "<label for='ssid1'>Network 2 SSID</label>"
  "<input id='ssid1' maxlength='32' autocomplete='off'>"
  "<label for='pass1'>Password</label>"
  "<input id='pass1' type='password' maxlength='64' autocomplete='new-password' placeholder='blank keeps saved password'>"
  "<label>"
  "<input id='wifi-open1' type='checkbox' style='width:auto'> Open network (no password)</label>"
  "<label for='ssid2'>Network 3 SSID</label>"
  "<input id='ssid2' maxlength='32' autocomplete='off'>"
  "<label for='pass2'>Password</label>"
  "<input id='pass2' type='password' maxlength='64' autocomplete='new-password' placeholder='blank keeps saved password'>"
  "<label>"
  "<input id='wifi-open2' type='checkbox' style='width:auto'> Open network (no password)</label>"
  "<label for='ssid3'>Network 4 SSID</label>"
  "<input id='ssid3' maxlength='32' autocomplete='off'>"
  "<label for='pass3'>Password</label>"
  "<input id='pass3' type='password' maxlength='64' autocomplete='new-password' placeholder='blank keeps saved password'>"
  "<label>"
  "<input id='wifi-open3' type='checkbox' style='width:auto'> Open network (no password)</label>"
  "<label for='ssid4'>Network 5 SSID</label>"
  "<input id='ssid4' maxlength='32' autocomplete='off'>"
  "<label for='pass4'>Password</label>"
  "<input id='pass4' type='password' maxlength='64' autocomplete='new-password' placeholder='blank keeps saved password'>"
  "<label>"
  "<input id='wifi-open4' type='checkbox' style='width:auto'> Open network (no password)</label>"
  "</fieldset>"
  "<label>Gateway endpoint (required)</label>"
  "<input id='url' placeholder='http://192.168.1.10:8080/api/v1/voice/turn'>"
  "<div class='muted' id='gateway-help'>For v1, use the full /api/v1/voice/turn endpoint.</div>"
  "<label id='token-label'>Device token (optional)</label>"
  "<input id='token' type='password' placeholder='blank keeps saved token'>"
  "<label for='sleep-seconds'>Battery sleep timeout (seconds)</label>"
  "<input id='sleep-seconds' type='number' min='5' max='3600' step='1' value='30'>"
  "<div class='muted'>5–3600 seconds of inactivity. No automatic sleep on USB power.</div>"
  "<fieldset>"
  "<legend>WireGuard VPN</legend>"
  "<label>"
  "<input id='wg-enabled' type='checkbox' style='width:auto'> Enable WireGuard</label>"
  "<label for='wg-address'>VPN IPv4 address</label>"
  "<input id='wg-address' type='text' placeholder='10.7.0.2' autocomplete='off'>"
  "<label for='wg-netmask'>VPN subnet mask</label>"
  "<input id='wg-netmask' type='text' placeholder='255.255.255.0' autocomplete='off'>"
  "<label for='wg-endpoint'>Server hostname or IPv4</label>"
  "<input id='wg-endpoint' type='text' placeholder='vpn.example.com' autocomplete='off'>"
  "<label for='wg-port'>Server UDP port</label>"
  "<input id='wg-port' type='number' placeholder='51820' autocomplete='off'>"
  "<label for='wg-public_key'>Peer public key</label>"
  "<input id='wg-public_key' type='text' placeholder='' autocomplete='off'>"
  "<label for='wg-private_key'>Device private key</label>"
  "<input id='wg-private_key' type='password' placeholder='blank keeps saved key' autocomplete='off'>"
  "<label for='wg-preshared_key'>Preshared key (optional)</label>"
  "<input id='wg-preshared_key' type='password' placeholder='blank keeps saved key' autocomplete='off'>"
  "<label for='wg-keepalive'>Persistent keepalive (seconds; 0 disables)</label>"
  "<input id='wg-keepalive' type='number' placeholder='25' autocomplete='off'>"
  "<label for='wg-ntp_server'>NTP server reachable before VPN</label>"
  "<input id='wg-ntp_server' type='text' placeholder='pool.ntp.org' autocomplete='off'>"
  "<label>"
  "<input id='wg-clear-psk' type='checkbox' style='width:auto'> Remove saved preshared key</label>"
  "<label>"
  "<input id='wg-full_tunnel' type='checkbox' style='width:auto'> Full IPv4 tunnel (AllowedIPs 0.0.0.0/0)</label>"
  "<div class='muted'>By default the VPN subnet is allowed. Use the gateway VPN address in that subnet. VPN and Wi-Fi/setup subnets must not overlap. Blank keys keep saved values. <span id='wg-keys'>"
  "</span>"
  "</div>"
  "<div class='muted' id='wg-status'>"
  "</div>"
  "</fieldset>"
  "<button class='save' onclick='saveCfg()'>Save &amp; Restart</button>"
  "<div class='muted' id='info'>"
  "</div>"
  "</div>"
  "<div class='card'>"
  "<b>Status</b>"
  "<div id='status' class='muted' style='margin-top:6px'>loading…</div>"
  "</div>"
  "<script>const $=x=>document.getElementById(x);const ssidId=i=>i?'ssid'+i:'ssid',passId=i=>i?'pass'+i:'pass';let savedWifi=[],savedToken=false;function updateProtocol(){const v2=$('protocol').value==='2';$('gateway-help').textContent=v2?'For v2, use only the Gateway base URL, e.g. http://192.168.1.10:8080.':'For v1, use the full /api/v1/voice/turn endpoint.';$('token-label').textContent=v2?'Device token (required for v2)':'Device token (optional)';}async function load(){try{const j=await (await fetch('/config')).json();savedWifi=j.wifi_networks||[{ssid:j.wifi_ssid||'',password_set:!!j.wifi_password_set}];for(let i=0;i<5;i++){const n=savedWifi[i]||{};$(ssidId(i)).value=n.ssid||'';$('wifi-open'+i).checked=!!n.ssid&&!n.password_set;}$('url').value=j.gateway_url||'';$('protocol').value=String(j.protocol_version||1);$('sleep-seconds').value=String(j.sleep_timeout_seconds??30);savedToken=!!j.device_token_set;$('wg-enabled').checked=!!j.wg_enabled;$('wg-full_tunnel').checked=!!j.wg_full_tunnel;$('wg-address').value=String(j.wg_address??\"\");$('wg-netmask').value=String(j.wg_netmask??\"255.255.255.0\");$('wg-endpoint').value=String(j.wg_endpoint??\"\");$('wg-port').value=String(j.wg_port??\"51820\");$('wg-public_key').value=String(j.wg_public_key??\"\");$('wg-keepalive').value=String(j.wg_keepalive??\"25\");$('wg-ntp_server').value=String(j.wg_ntp_server??\"pool.ntp.org\");$('wg-keys').textContent='Private key: '+(j.wg_private_key_set?'saved':'missing')+'; PSK: '+(j.wg_preshared_key_set?'saved':'none');$('wg-status').textContent='WireGuard: '+(j.wg_status||'disabled');updateProtocol();$('ip').textContent=j.ip&&j.ip!=='0.0.0.0'?'· '+j.ip:'';$('status').textContent=j.wifi_connected?'Wi‑Fi connected · '+(j.active_ssid||'')+' · '+j.ip:'Setup access point · '+j.ap_ip;}catch(e){$('status').textContent='status unavailable';}}async function scanWifi(refresh=false){const sel=$('scan');sel.replaceChildren(new Option('scanning…',''));try{const j=await (await fetch(refresh?'/wifi_scan?refresh=1':'/wifi_scan')).json();if(!j.ok&&j.scanning){setTimeout(()=>scanWifi(),1000);return;}if(!j.ok)throw new Error('scan failed');sel.replaceChildren(new Option('— select network —',''));for(const n of (j.networks||[])){sel.add(new Option(n.ssid+' ('+n.rssi+' dBm)',n.ssid));}sel.onchange=()=>{if(sel.value)$(ssidId(Number($('wifi-slot').value||0))).value=sel.value;};}catch(e){sel.replaceChildren(new Option('scan failed',''));}}async function saveCfg(){const url=$('url').value.trim(),protocol=$('protocol').value;if(!url){$('info').textContent='Enter Gateway endpoint';return;}if(!url.startsWith('http://')&&!url.startsWith('https://')){$('info').textContent='Gateway endpoint must start with http:// or https://';return;}if(protocol==='2'){if(!$('token').value&&!savedToken){$('info').textContent='Enter the required device token for v2';return;}try{const parsed=new URL(url);if(parsed.pathname!=='/'||parsed.search||parsed.hash){$('info').textContent='For v2, enter the Gateway base URL without a path';return;}}catch(e){$('info').textContent='Enter a valid Gateway base URL';return;}}const networks=Array.from({length:5},(_,i)=>({ssid:$(ssidId(i)).value,password:$(passId(i)).value,open:$('wifi-open'+i).checked}));if(!networks.some(n=>n.ssid)){$('info').textContent='Enter at least one Wi-Fi network (SSID)';return;}const seen=new Set();for(let i=0;i<5;i++){const n=networks[i];if(!n.ssid)continue;if(seen.has(n.ssid)){$('info').textContent='Duplicate Wi-Fi network: '+n.ssid;return;}seen.add(n.ssid);if(!n.open&&!n.password&&(!savedWifi[i]?.password_set||savedWifi[i]?.ssid!==n.ssid)){$('info').textContent='Network '+(i+1)+': enter password or select Open network';return;}if(!n.open&&n.password&&n.password.length<8){$('info').textContent='Network '+(i+1)+': password must have at least 8 characters';return;}}const sleep=$('sleep-seconds').value;if(!/^[0-9]+$/.test(sleep)||Number(sleep)<5||Number(sleep)>3600){$('info').textContent='Sleep timeout must be a whole number from 5 to 3600 seconds';return;}const body=new URLSearchParams({gateway_url:url,device_token:$('token').value,protocol_version:protocol,sleep_timeout_seconds:sleep});for(let i=0;i<5;i++){body.set('wifi'+i+'_ssid',networks[i].ssid);body.set('wifi'+i+'_password',networks[i].password);body.set('wifi'+i+'_open',networks[i].open?'1':'0');}body.set('wg_enabled',$('wg-enabled').checked?'1':'0');body.set('wg_full_tunnel',$('wg-full_tunnel').checked?'1':'0');body.set('wg_clear_psk',$('wg-clear-psk').checked?'1':'0');body.set('wg_address',$('wg-address').value.trim());body.set('wg_netmask',$('wg-netmask').value.trim());body.set('wg_endpoint',$('wg-endpoint').value.trim());body.set('wg_port',$('wg-port').value.trim());body.set('wg_public_key',$('wg-public_key').value.trim());body.set('wg_private_key',$('wg-private_key').value.trim());body.set('wg_preshared_key',$('wg-preshared_key').value.trim());body.set('wg_keepalive',$('wg-keepalive').value.trim());body.set('wg_ntp_server',$('wg-ntp_server').value.trim());$('info').textContent='saving…';const r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});$('info').textContent=r.ok?'saved; restarting…':'save failed';}async function refreshWg(){try{const j=await(await fetch('/config')).json();$('wg-status').textContent='WireGuard: '+j.wg_status;$('status').textContent=j.wifi_connected?'Wi‑Fi connected · '+(j.active_ssid||'')+' · '+j.ip:'Setup access point · '+j.ap_ip;}catch(e){}}load();scanWifi();setInterval(refreshWg,3000);</script>"
  "</body>"
  "</html>";

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
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, k_html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t h_portal_redirect(httpd_req_t *req, httpd_err_code_t error) {
  (void)error;
  if (!allow_setup_client(req)) return ESP_OK;
  httpd_resp_set_status(req, "302 Found");
  httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req,
      "<html><body><a href='http://192.168.4.1/'>Open Hermes StickS3 setup</a></body></html>",
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
  char body[4096]; size_t used = 0;
  used += (size_t)snprintf(body, sizeof(body), "{\"wifi_ssid\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wifi[0].ssid)) return ESP_FAIL;
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
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"gateway_url\":");
  if (!json_string(body, sizeof(body), &used, s_settings->gateway_url)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"device_id\":");
  if (!json_string(body, sizeof(body), &used, s_settings->device_id)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wg_address\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wireguard.address)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wg_netmask\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wireguard.netmask)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wg_endpoint\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wireguard.endpoint)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wg_public_key\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wireguard.public_key)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used, ",\"wg_ntp_server\":");
  if (!json_string(body, sizeof(body), &used, s_settings->wireguard.ntp_server)) return ESP_FAIL;
  used += (size_t)snprintf(body + used, sizeof(body) - used,
      ",\"wg_enabled\":%s,\"wg_full_tunnel\":%s,\"wg_port\":%u,\"wg_keepalive\":%u,"
      "\"wg_private_key_set\":%s,\"wg_preshared_key_set\":%s,\"wg_status\":\"%s\"",
      s_settings->wireguard.enabled ? "true" : "false",
      s_settings->wireguard.full_tunnel ? "true" : "false",
      s_settings->wireguard.port, s_settings->wireguard.keepalive,
      s_settings->wireguard.private_key[0] ? "true" : "false",
      s_settings->wireguard.preshared_key[0] ? "true" : "false",
      voice_wireguard_status());
  used += (size_t)snprintf(body + used, sizeof(body) - used,
      ",\"sleep_timeout_seconds\":%lu,\"protocol_version\":%ld,\"ip\":\"%s\",\"ap_ip\":\"%s\",\"wifi_connected\":%s,\"wifi_password_set\":%s,\"device_token_set\":%s}",
      (unsigned long)s_settings->sleep_timeout_seconds,
      (long)s_settings->protocol_version,
      ip, ap_addr, sta_ip.ip.addr ? "true" : "false",
      s_settings->wifi[0].password[0] ? "true" : "false",
      s_settings->device_token[0] ? "true" : "false");
  return send_json(req, body);
}

static esp_err_t h_config_post(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
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

static esp_err_t h_wifi_scan(httpd_req_t *req) {
  if (!allow_setup_client(req)) return ESP_OK;
  if (strstr(req->uri, "refresh=1")) voice_config_httpd_setup_ap_started();
  char body[sizeof(s_scan_json)];
  xSemaphoreTake(s_scan_mutex, portMAX_DELAY);
  bool ready = s_scan_ready;
  strlcpy(body, ready ? s_scan_json :
          "{\"ok\":false,\"scanning\":true,\"networks\":[]}", sizeof(body));
  xSemaphoreGive(s_scan_mutex);
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
  esp_netif_t *ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
  esp_err_t dns_err = voice_captive_dns_start(ap);
  if (dns_err != ESP_OK)
    ESP_LOGE(TAG, "captive DNS start failed: %s", esp_err_to_name(dns_err));
  wifi_mode_t mode = WIFI_MODE_NULL;
  esp_err_t err = esp_wifi_get_mode(&mode);
  if (err == ESP_OK && mode != WIFI_MODE_APSTA) err = ESP_ERR_INVALID_STATE;
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
  httpd_register_uri_handler(s_server, &root); httpd_register_uri_handler(s_server, &get_cfg);
  httpd_register_uri_handler(s_server, &post_cfg); httpd_register_uri_handler(s_server, &scan);
  httpd_register_err_handler(s_server, HTTPD_404_NOT_FOUND, h_portal_redirect);
  wifi_mode_t mode = WIFI_MODE_NULL;
  if (esp_wifi_get_mode(&mode) == ESP_OK && mode == WIFI_MODE_APSTA)
    voice_config_httpd_setup_ap_started();
  ESP_LOGI(TAG, "settings UI started on port 80");
}
#else
void voice_config_httpd_start(voice_settings_t *settings) { (void)settings; }
void voice_config_httpd_setup_ap_started(void) {}
bool voice_config_httpd_wifi_scanning(void) { return false; }
#endif
