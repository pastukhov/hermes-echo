#include "hardware.h"
#include "board_sticks3.h"
#include "audio_capture.h"
#include "audio_playback.h"
#include "screen_ui.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "sticks3";
static bool s_audio_ready;
static bool s_button_ready;
static spi_device_handle_t s_lcd;
static bool s_lcd_ready;
static bool s_wifi_initialized;
static bool s_wifi_handlers_registered;
static bool s_wifi_connected;
static esp_netif_t *s_wifi_sta_netif;
static esp_netif_t *s_wifi_ap_netif;
static uint16_t *s_screen;
static state_t s_screen_state = (state_t)-1;
static int s_screen_phase = -1;
static bool s_screen_wifi;
static bool s_screen_timing_reported;

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data) {
  (void)arg;
  if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    ESP_LOGI(TAG, "Wi-Fi STA started");
  } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    s_wifi_connected = false;
    ESP_LOGW(TAG, "Wi-Fi disconnected");
  } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
    s_wifi_connected = true;
    ESP_LOGI(TAG, "Wi-Fi got IP: " IPSTR, IP2STR(&event->ip_info.ip));
  }
}

static bool wifi_init_once(bool need_sta, bool need_ap) {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    if (nvs_flash_erase() != ESP_OK) return false;
    err = nvs_flash_init();
  }
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(TAG, "NVS init failed: %s", esp_err_to_name(err));
    return false;
  }
  err = esp_netif_init();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return false;
  err = esp_event_loop_create_default();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return false;
  if (need_sta && !s_wifi_sta_netif) {
    s_wifi_sta_netif = esp_netif_create_default_wifi_sta();
    if (!s_wifi_sta_netif) return false;
  }
  if (need_ap && !s_wifi_ap_netif) {
    s_wifi_ap_netif = esp_netif_create_default_wifi_ap();
    if (!s_wifi_ap_netif) return false;
  }
  if (!s_wifi_initialized) {
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK) return false;
    s_wifi_initialized = true;
  }
  if (!s_wifi_handlers_registered) {
    if (esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                   &wifi_event_handler, NULL) != ESP_OK ||
        esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                   &wifi_event_handler, NULL) != ESP_OK) {
      return false;
    }
    s_wifi_handlers_registered = true;
  }
  return true;
}

static void init_m5pm1(void) {
  i2c_master_bus_config_t bc = {.i2c_port = I2C_NUM_0, .sda_io_num = BOARD_I2C_SDA_GPIO,
    .scl_io_num = BOARD_I2C_SCL_GPIO, .clk_source = I2C_CLK_SRC_DEFAULT,
    .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
  i2c_master_bus_handle_t bus = NULL; i2c_master_dev_handle_t dev = NULL;
  if (i2c_new_master_bus(&bc, &bus) != ESP_OK) return;
  i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7,
    .device_address = 0x6e, .scl_speed_hz = 100000};
  if (i2c_master_bus_add_device(bus, &dc, &dev) != ESP_OK) { i2c_del_master_bus(bus); return; }
  uint8_t cfg[] = {0x09, 0x00}; i2c_master_transmit(dev, cfg, sizeof(cfg), 200);
  uint8_t dir[] = {0x10, 0x0c}; i2c_master_transmit(dev, dir, sizeof(dir), 200);
  uint8_t out[] = {0x11, 0x0c}; i2c_master_transmit(dev, out, sizeof(out), 200);
  i2c_master_bus_rm_device(dev); i2c_del_master_bus(bus);
  ESP_LOGI(TAG, "M5PM1 LCD and speaker rails enabled");
}

static void lcd_cmd(uint8_t cmd) {
  gpio_set_level(BOARD_LCD_DC_GPIO, 0);
  spi_transaction_t t = {.length = 8, .tx_buffer = &cmd};
  spi_device_polling_transmit(s_lcd, &t);
}
static void lcd_data(const uint8_t *data, size_t len) {
  gpio_set_level(BOARD_LCD_DC_GPIO, 1);
  spi_transaction_t t = {.length = len * 8, .tx_buffer = data};
  spi_device_polling_transmit(s_lcd, &t);
}
static void lcd_init(void) {
  if (s_lcd_ready) return;
  init_m5pm1();
  gpio_set_direction(BOARD_LCD_DC_GPIO, GPIO_MODE_OUTPUT);
  gpio_set_direction(BOARD_LCD_RST_GPIO, GPIO_MODE_OUTPUT);
  gpio_set_direction(BOARD_LCD_BL_GPIO, GPIO_MODE_OUTPUT);
  gpio_set_level(BOARD_LCD_RST_GPIO, 0); vTaskDelay(pdMS_TO_TICKS(20));
  gpio_set_level(BOARD_LCD_RST_GPIO, 1); vTaskDelay(pdMS_TO_TICKS(120));
  spi_bus_config_t bus = {.mosi_io_num = BOARD_LCD_MOSI_GPIO, .miso_io_num = -1,
                          .sclk_io_num = BOARD_LCD_SCLK_GPIO, .quadwp_io_num = -1,
                          .quadhd_io_num = -1, .max_transfer_sz = 270};
  spi_bus_initialize(SPI3_HOST, &bus, SPI_DMA_CH_AUTO);
  spi_device_interface_config_t dev = {.clock_speed_hz = 40000000, .mode = 0,
                                       .spics_io_num = BOARD_LCD_CS_GPIO,
                                       .queue_size = 1};
  spi_bus_add_device(SPI3_HOST, &dev, &s_lcd);
  lcd_cmd(0x01); vTaskDelay(pdMS_TO_TICKS(120));
  lcd_cmd(0x11); vTaskDelay(pdMS_TO_TICKS(120));
  uint8_t colmod = 0x55; lcd_cmd(0x3A); lcd_data(&colmod, 1);
  uint8_t madctl = 0x00; lcd_cmd(0x36); lcd_data(&madctl, 1);
  lcd_cmd(0x21); /* M5StickS3 panel requires inversion. */
  lcd_cmd(0x29); gpio_set_level(BOARD_LCD_BL_GPIO, 1); s_lcd_ready = true;
}
static void lcd_fill(uint16_t color) {
  lcd_init();
  uint8_t area[4] = {0, 52, 0, 186}; lcd_cmd(0x2A); lcd_data(area, 4);
  area[0] = 0; area[1] = 40; area[2] = 1; area[3] = 23; lcd_cmd(0x2B); lcd_data(area, 4);
  lcd_cmd(0x2C);
  uint8_t line[270];
  for (size_t i = 0; i < sizeof(line); i += 2) { line[i] = color >> 8; line[i + 1] = color & 0xff; }
  for (int y = 0; y < 240; ++y) lcd_data(line, sizeof(line));
}

#define SCREEN_W 135
#define SCREEN_H 240
#define C_BG 0x0083
#define C_PANEL 0x08C5
#define C_MUTED 0x74B3
#define C_WHITE 0xEF9F
#define C_LINE 0x1988

static const uint8_t FONT5X7[26][7] = {
  {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
  {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
  {14,17,16,23,17,17,15},{17,17,17,31,17,17,17},{14,4,4,4,4,4,14},
  {7,2,2,2,18,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
  {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
  {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
  {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
  {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
  {17,17,10,4,4,4,4},{31,1,2,4,8,16,31}
};
static const uint8_t FONT_CYRILLIC_5X7[32][7] = {
  {14,17,17,31,17,17,17}, {30,17,17,30,17,17,30}, {30,17,17,30,17,17,30},
  {31,16,16,16,16,16,16}, {6,10,10,10,18,31,17}, {31,16,16,30,16,16,31},
  {21,21,14,4,14,21,21}, {14,17,1,6,1,17,14}, {15,16,16,14,1,1,30},
  {4,10,0,19,21,25,17}, {17,18,20,24,20,18,17}, {16,16,16,16,16,16,31},
  {7,9,9,9,17,17,17}, {17,27,21,21,17,17,17}, {17,25,21,19,17,17,17},
  {31,17,17,17,17,17,17}, {30,17,17,30,16,16,16}, {14,17,16,16,16,17,14},
  {31,4,4,4,4,4,4}, {17,17,10,4,4,8,16}, {4,14,21,21,21,14,4},
  {17,17,10,4,10,17,17}, {17,17,17,17,17,31,1}, {17,17,17,15,1,1,1},
  {21,21,21,21,21,21,31}, {21,21,21,21,21,31,1}, {24,8,8,14,9,9,14},
  {17,17,25,21,21,25,17}, {16,16,30,17,17,17,30}, {14,17,1,7,1,17,14},
  {17,21,21,29,21,21,17}, {15,17,17,15,5,9,17}
};

static uint32_t screen_utf8_next(const char **text) {
  const uint8_t *p = (const uint8_t *)*text;
  uint32_t cp;
  if (p[0] < 0x80) { ++*text; return p[0]; }
  if ((p[0] & 0xE0) == 0xC0) {
    cp = ((uint32_t)(p[0] & 0x1F) << 6) | (p[1] & 0x3F); *text += 2;
  } else if ((p[0] & 0xF0) == 0xE0) {
    cp = ((uint32_t)(p[0] & 0x0F) << 12) | ((uint32_t)(p[1] & 0x3F) << 6) | (p[2] & 0x3F); *text += 3;
  } else { ++*text; return '?'; }
  return cp;
}

static const uint8_t *screen_glyph(uint32_t cp) {
  if (cp >= 'a' && cp <= 'z') cp -= 'a' - 'A';
  if (cp >= 'A' && cp <= 'Z') return FONT5X7[cp - 'A'];
  if (cp == 0x0401 || cp == 0x0451) cp = 0x0415; /* Ё/ё */
  if (cp >= 0x0430 && cp <= 0x044F) cp -= 0x20;
  if (cp >= 0x0410 && cp <= 0x042F) return FONT_CYRILLIC_5X7[cp - 0x0410];
  return NULL;
}

static void screen_pixel(int x, int y, uint16_t color) {
  if (!s_screen || x < 0 || x >= SCREEN_W || y < 0 || y >= SCREEN_H) return;
  s_screen[y * SCREEN_W + x] = color;
}

static void screen_rect(int x, int y, int w, int h, uint16_t color) {
  for (int py = y; py < y + h; ++py)
    for (int px = x; px < x + w; ++px) screen_pixel(px, py, color);
}

static void screen_circle(int cx, int cy, int radius, uint16_t color) {
  for (int y = -radius; y <= radius; ++y)
    for (int x = -radius; x <= radius; ++x)
      if (x * x + y * y <= radius * radius) screen_pixel(cx + x, cy + y, color);
}

static void screen_text(const char *text, int x, int y, int scale, uint16_t color) {
  int width = 0;
  const char *count = text;
  while (*count) { (void)screen_utf8_next(&count); width += 6 * scale; }
  x -= width / 2;
  while (*text) {
    uint32_t cp = screen_utf8_next(&text);
    const uint8_t *rows = screen_glyph(cp);
    if (!rows) { x += 6 * scale; continue; }
    for (int row = 0; row < 7; ++row)
      for (int col = 0; col < 5; ++col)
        if (rows[row] & (1U << (4 - col))) screen_rect(x + col * scale, y + row * scale, scale, scale, color);
    x += 6 * scale;
  }
}

static void screen_wifi_icon(bool connected) {
  uint16_t color = connected ? 0x38B8 : C_MUTED;
  screen_rect(111, 21, 3, 3, color);
  screen_rect(117, 16, 3, 8, color);
  screen_rect(123, 11, 3, 13, color);
}

static void screen_draw_icon(screen_ui_view_t view, int phase) {
  const int cx = 67, cy = 104;
  screen_circle(cx, cy, 37, view.accent);
  screen_circle(cx, cy, 32, C_PANEL);
  switch (view.icon) {
    case SCREEN_ICON_LISTENING:
      screen_rect(cx - 3, cy - 17, 6, 24, C_WHITE);
      screen_circle(cx, cy - 17, 3, C_WHITE);
      screen_rect(cx - 9, cy + 3, 3, 8, C_WHITE);
      screen_rect(cx + 6, cy + 3, 3, 8, C_WHITE);
      screen_rect(cx - 9, cy + 9, 18, 3, C_WHITE);
      screen_rect(cx - 1, cy + 12, 3, 6, C_WHITE);
      screen_rect(cx - 7, cy + 17, 15, 3, C_WHITE);
      break;
    case SCREEN_ICON_THINKING:
      for (int i = 0; i < 8; ++i) {
        int dot = (i + phase) & 7;
        static const int dx[8] = {0,18,26,18,0,-18,-26,-18};
        static const int dy[8] = {-26,-18,0,18,26,18,0,-18};
        screen_circle(cx + dx[i], cy + dy[i], dot == 0 ? 5 : 3, dot < 3 ? view.accent : C_MUTED);
      }
      break;
    case SCREEN_ICON_SPEAKING:
      for (int i = 0; i < 7; ++i) {
        int h = 8 + ((i * 7 + phase * 5) % 19);
        screen_rect(cx - 22 + i * 7, cy - h / 2, 4, h, C_WHITE);
      }
      break;
    case SCREEN_ICON_ERROR:
      screen_rect(cx - 3, cy - 19, 6, 25, C_WHITE);
      screen_circle(cx, cy + 14, 4, C_WHITE);
      break;
    case SCREEN_ICON_IDLE:
    default:
      screen_circle(cx, cy, 17, view.accent);
      screen_circle(cx, cy, 12, C_PANEL);
      screen_circle(cx, cy, 6, view.accent);
      screen_circle(cx - 23, cy, 3, C_WHITE);
      screen_circle(cx + 23, cy, 3, C_WHITE);
      break;
  }
}

static void screen_flush(void) {
  uint8_t row[SCREEN_W * 2];
  uint8_t area[4] = {0, 52, 0, 186};
  lcd_cmd(0x2A); lcd_data(area, sizeof(area));
  area[0] = 0; area[1] = 40; area[2] = 1; area[3] = 23;
  lcd_cmd(0x2B); lcd_data(area, sizeof(area)); lcd_cmd(0x2C);
  for (int y = 0; y < SCREEN_H; ++y) {
    for (int x = 0; x < SCREEN_W; ++x) {
      uint16_t c = s_screen[y * SCREEN_W + x];
      row[x * 2] = (uint8_t)(c >> 8); row[x * 2 + 1] = (uint8_t)c;
    }
    lcd_data(row, sizeof(row));
  }
}

void board_sticks3_display_update(state_t state, uint32_t now_ms) {
  int phase = (int)(now_ms / 180U);
  if (state == s_screen_state && phase == s_screen_phase && s_wifi_connected == s_screen_wifi) return;
  if (!s_screen) s_screen = heap_caps_malloc(SCREEN_W * SCREEN_H * sizeof(*s_screen), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!s_screen) s_screen = heap_caps_malloc(SCREEN_W * SCREEN_H * sizeof(*s_screen), MALLOC_CAP_8BIT);
  if (!s_screen) return;
  lcd_init();
  screen_ui_view_t view = screen_ui_view(state);
  for (int i = 0; i < SCREEN_W * SCREEN_H; ++i) s_screen[i] = C_BG;
  screen_text("ГЕРМЕС", 42, 13, 1, C_WHITE);
  screen_wifi_icon(s_wifi_connected);
  screen_rect(10, 32, 115, 1, C_LINE);
  screen_draw_icon(view, phase);
  screen_text(view.title, 67, 158, 2, C_WHITE);
  screen_text(view.hint, 67, 184, 1, C_MUTED);
  screen_rect(45, 216, 45, 1, C_LINE);
  screen_text("ГОЛОСОВОЙ ТЕРМИНАЛ", 67, 222, 1, C_MUTED);
  int64_t render_start_us = esp_timer_get_time();
  screen_flush();
  if (!s_screen_timing_reported) {
    ESP_LOGI(TAG, "status screen full-frame time: %lld ms",
             (long long)((esp_timer_get_time() - render_start_us) / 1000));
    s_screen_timing_reported = true;
  }
  s_screen_state = state;
  s_screen_phase = phase;
  s_screen_wifi = s_wifi_connected;
}

void board_sticks3_log_memory(void) {
  uint32_t flash = 0;
  if (esp_flash_get_size(NULL, &flash) == ESP_OK) {
    ESP_LOGI(TAG, "Flash detected: %u bytes", (unsigned)flash);
  }
  ESP_LOGI(TAG, "PSRAM total: %u bytes", (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM));
}

bool board_sticks3_wifi_start(const char *ssid, const char *password) {
  if (!ssid || !ssid[0] || !password) return false;
  if (!wifi_init_once(true, true)) return false;
  wifi_config_t cfg = {0};
  strncpy((char *)cfg.sta.ssid, ssid, sizeof(cfg.sta.ssid) - 1);
  strncpy((char *)cfg.sta.password, password, sizeof(cfg.sta.password) - 1);
  wifi_config_t ap = {0};
  const char *setup_ssid = "Hermes-StickS3-Setup";
  strncpy((char *)ap.ap.ssid, setup_ssid, sizeof(ap.ap.ssid) - 1);
  ap.ap.ssid_len = (uint8_t)strlen(setup_ssid);
  ap.ap.authmode = WIFI_AUTH_OPEN;
  ap.ap.max_connection = 4;
  esp_err_t start_err;
  if (esp_wifi_set_mode(WIFI_MODE_APSTA) != ESP_OK ||
      esp_wifi_set_config(WIFI_IF_AP, &ap) != ESP_OK ||
      esp_wifi_set_config(WIFI_IF_STA, &cfg) != ESP_OK) return false;
  start_err = esp_wifi_start();
  if (start_err != ESP_OK && start_err != ESP_ERR_INVALID_STATE) return false;
  if (esp_wifi_connect() != ESP_OK) return false;
  ESP_LOGI(TAG, "Wi-Fi station and setup AP start requested");
  return true;
}

bool board_sticks3_wifi_start_ap(const char *ssid) {
  if (!ssid || !ssid[0]) ssid = "Hermes-StickS3-Setup";
  if (!wifi_init_once(true, true)) return false;
  wifi_config_t cfg = {0};
  strncpy((char *)cfg.ap.ssid, ssid, sizeof(cfg.ap.ssid) - 1);
  cfg.ap.ssid_len = (uint8_t)strlen((char *)cfg.ap.ssid);
  cfg.ap.authmode = WIFI_AUTH_OPEN;
  cfg.ap.max_connection = 4;
  esp_err_t start_err;
  if (esp_wifi_set_mode(WIFI_MODE_AP) != ESP_OK ||
      esp_wifi_set_config(WIFI_IF_AP, &cfg) != ESP_OK) return false;
  start_err = esp_wifi_start();
  if (start_err != ESP_OK && start_err != ESP_ERR_INVALID_STATE) return false;
  ESP_LOGI(TAG, "Setup AP started: SSID=%s, IP=192.168.4.1", ssid);
  return true;
}

uint32_t hw_clock_ms(void) {
  return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

bool hw_button_raw(void) {
  if (!s_button_ready) {
    gpio_config_t cfg = {
      .pin_bit_mask = 1ULL << BOARD_BUTTON_PIN,
      .mode = GPIO_MODE_INPUT,
      .pull_up_en = GPIO_PULLUP_ENABLE,
      .pull_down_en = GPIO_PULLDOWN_DISABLE,
      .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    s_button_ready = true;
  }
  return gpio_get_level(BOARD_BUTTON_PIN) == 0;
}

void hw_led_write(uint16_t rgb565) {
  static uint16_t last = 0xffff;
  if (last == rgb565 && s_lcd_ready) return;
  last = rgb565;
  lcd_fill(rgb565);
}

bool hw_report_error(const char *what) {
  ESP_LOGE(TAG, "hardware error: %s", what ? what : "(null)");
  return false;
}

static void ensure_audio(void) {
  if (s_audio_ready) return;
  const audio_capture_config_t cap = {
    .sample_rate = 16000, .bits_per_sample = 16, .channel_count = 1,
    .buffer_frame_size = 512, .queue_size = 8
  };
  const audio_playback_config_t play = {
    .sample_rate = 16000, .bits_per_sample = 16, .channel_count = 1,
    .buffer_frame_size = 512, .queue_size = 8
  };
  if (audio_capture_init(&cap) != ESP_OK ||
      audio_playback_init(&play) != ESP_OK) {
    ESP_LOGE(TAG, "audio init failed");
    return;
  }
  s_audio_ready = true;
}

void hw_audio_capture_start(void) {
  ensure_audio();
  (void)audio_capture_start();
}
void hw_audio_capture_stop(void) { (void)audio_capture_stop(); }
size_t hw_audio_capture_read(uint8_t *buf, size_t max_len) {
  return audio_capture_read(buf, max_len, 0);
}
void hw_audio_playback_start(const void *data, size_t size) {
  (void)data; (void)size;
  ensure_audio();
  (void)audio_playback_start();
}
void hw_audio_playback_stop(void) { (void)audio_playback_stop(); }
size_t hw_audio_playback_write(const uint8_t *buf, size_t size) {
  return audio_playback_write(buf, size, 0) == ESP_OK ? size : 0;
}
bool hw_audio_playback_drained(void) {
  return audio_playback_get_buffer_level() == 0;
}
