#include "hardware.h"
#include "board_sticks3.h"
#include "audio_capture.h"
#include "audio_playback.h"
#include "screen_ui.h"
#include "screen_font.h"
#include "voice_config_httpd.h"
#include "voice_wifi_setup.h"

#include <stdio.h>
#include <string.h>

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
static volatile bool s_sta_associated;
static bool s_wifi_has_credentials;
static voice_wifi_setup_t s_wifi_setup;
static char s_setup_ssid[33];
static TaskHandle_t s_wifi_manager_task;
static esp_netif_t *s_wifi_sta_netif;
static esp_netif_t *s_wifi_ap_netif;
static uint16_t *s_screen;
static state_t s_screen_state = (state_t)-1;
static int s_screen_phase = -1;
static screen_processing_phase_t s_screen_processing_phase = SCREEN_PROCESSING_THINKING;
static bool s_screen_wifi;
static bool s_screen_timing_reported;
static char s_screen_device_id[16];
static bool wifi_init_once(bool need_sta, bool need_ap);

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data) {
  (void)arg;
  if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    ESP_LOGI(TAG, "Wi-Fi STA started");
  } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
    s_sta_associated = true;
  } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    s_sta_associated = false;
    s_wifi_connected = false;
    ESP_LOGW(TAG, "Wi-Fi disconnected");
  } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
    s_wifi_connected = true;
    ESP_LOGI(TAG, "Wi-Fi got IP: " IPSTR, IP2STR(&event->ip_info.ip));
  }
}

static void wifi_manager(void *arg) {
  (void)arg;
  uint32_t last_connect_attempt_ms = (uint32_t)(esp_timer_get_time() / 1000ULL);
  for (;;) {
    uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000ULL);
    voice_wifi_setup_set_connected(&s_wifi_setup, s_wifi_connected, now_ms);
    if (voice_wifi_setup_should_stop_ap(&s_wifi_setup)) {
      esp_err_t err = esp_wifi_set_mode(WIFI_MODE_STA);
      if (err == ESP_OK) {
        voice_wifi_setup_set_ap_active(&s_wifi_setup, false);
        ESP_LOGI(TAG, "Home Wi-Fi connected; setup AP stopped");
      } else {
        ESP_LOGE(TAG, "Failed to stop setup AP: %s", esp_err_to_name(err));
      }
    } else if (voice_wifi_setup_should_start_ap(&s_wifi_setup, now_ms)) {
      esp_err_t err = esp_wifi_set_mode(WIFI_MODE_APSTA);
      if (err == ESP_OK) {
        voice_wifi_setup_set_ap_active(&s_wifi_setup, true);
        ESP_LOGI(TAG, "Setup AP started after Wi-Fi timeout: SSID=%s", s_setup_ssid);
        voice_config_httpd_setup_ap_started();
      } else {
        ESP_LOGE(TAG, "Failed to start setup AP: %s", esp_err_to_name(err));
      }
    }
    if (s_wifi_has_credentials && !s_wifi_connected && !s_sta_associated &&
        (uint32_t)(now_ms - last_connect_attempt_ms) >= 5000U) {
      esp_err_t err = esp_wifi_connect();
      if (err != ESP_OK && err != ESP_ERR_WIFI_CONN)
        ESP_LOGW(TAG, "Wi-Fi retry failed: %s", esp_err_to_name(err));
      last_connect_attempt_ms = now_ms;
    }
    vTaskDelay(pdMS_TO_TICKS(500));
  }
}

static bool wifi_start_with_setup(const char *ssid, const char *password,
                                  bool configured) {
  if (!wifi_init_once(true, true)) return false;
  if (esp_wifi_set_mode(WIFI_MODE_APSTA) != ESP_OK) return false;
  uint8_t mac[6];
  if (esp_wifi_get_mac(WIFI_IF_STA, mac) != ESP_OK ||
      !voice_wifi_setup_ssid(s_setup_ssid, sizeof(s_setup_ssid), mac)) return false;
  wifi_config_t ap = {0};
  strncpy((char *)ap.ap.ssid, s_setup_ssid, sizeof(ap.ap.ssid) - 1);
  ap.ap.ssid_len = (uint8_t)strlen(s_setup_ssid);
  ap.ap.authmode = WIFI_AUTH_OPEN;
  ap.ap.max_connection = 4;
  /* Configure both interfaces before start; switch to STA-only before
   * broadcasting when saved credentials exist. */
  if (esp_wifi_set_config(WIFI_IF_AP, &ap) != ESP_OK) return false;
  if (configured) {
    wifi_config_t sta = {0};
    strncpy((char *)sta.sta.ssid, ssid, sizeof(sta.sta.ssid) - 1);
    strncpy((char *)sta.sta.password, password, sizeof(sta.sta.password) - 1);
    if (esp_wifi_set_config(WIFI_IF_STA, &sta) != ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK) return false;
  }
  voice_wifi_setup_init(&s_wifi_setup, configured,
                        (uint32_t)(esp_timer_get_time() / 1000ULL));
  voice_wifi_setup_set_ap_active(&s_wifi_setup, !configured);
  s_wifi_has_credentials = configured;
  s_wifi_connected = false;
  s_sta_associated = false;
  esp_err_t err = esp_wifi_start();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return false;
  if (configured) {
    err = esp_wifi_connect();
    if (err != ESP_OK) ESP_LOGW(TAG, "Initial Wi-Fi connect: %s", esp_err_to_name(err));
    if (!s_wifi_manager_task &&
        xTaskCreate(wifi_manager, "wifi_manager", 4096, NULL, 4,
                    &s_wifi_manager_task) != pdPASS) return false;
    ESP_LOGI(TAG, "Wi-Fi STA started; setup AP will start after 60s if needed");
  } else {
    ESP_LOGI(TAG, "Setup AP started: SSID=%s, IP=192.168.4.1", s_setup_ssid);
  }
  return true;
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
  uint8_t cfg[] = {0x09, 0x00};
  esp_err_t err = i2c_master_transmit(dev, cfg, sizeof(cfg), 200);
  const uint8_t regs[] = {0x16, 0x13, 0x10, 0x11};
  for (size_t i = 0; i < sizeof(regs) && err == ESP_OK; ++i) {
    uint8_t value = 0;
    err = i2c_master_transmit_receive(dev, &regs[i], 1, &value, 1, 200);
    if (err != ESP_OK) break;
    value = i < 2 ? (uint8_t)(value & ~0x0c) : (uint8_t)(value | 0x0c);
    uint8_t write[] = {regs[i], value};
    err = i2c_master_transmit(dev, write, sizeof(write), 200);
  }
  i2c_master_bus_rm_device(dev); i2c_del_master_bus(bus);
  if (err == ESP_OK) ESP_LOGI(TAG, "M5PM1 LCD and speaker rails enabled");
  else ESP_LOGE(TAG, "M5PM1 rail setup failed: %s", esp_err_to_name(err));
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
#define SCREEN_W 135
#define SCREEN_H 240
#define C_BG 0x0083
#define C_PANEL 0x08C5
#define C_MUTED 0x74B3
#define C_WHITE 0xEF9F
#define C_LINE 0x1988


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

static void screen_hint(const char *hint) {
  const char *newline = strchr(hint, '\n');
  if (!newline) {
    screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 67, 184,
                              SCREEN_FONT_HINT, hint, C_MUTED);
    return;
  }
  char first[48];
  size_t length = (size_t)(newline - hint);
  if (length >= sizeof(first)) return;
  memcpy(first, hint, length);
  first[length] = '\0';
  screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 67, 181,
                            SCREEN_FONT_HINT, first, C_MUTED);
  screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 67, 196,
                            SCREEN_FONT_HINT, newline + 1, C_MUTED);
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

void board_sticks3_display_set_device_id(const char *device_id) {
  snprintf(s_screen_device_id, sizeof(s_screen_device_id), "ID %s",
           device_id ? device_id : "");
  s_screen_state = (state_t)-1;
}

void board_sticks3_display_update(state_t state, uint32_t now_ms,
                                 screen_processing_phase_t processing_phase) {
  int phase = (int)(now_ms / 180U);
  if (state == s_screen_state && phase == s_screen_phase &&
      processing_phase == s_screen_processing_phase &&
      s_wifi_connected == s_screen_wifi) return;
  if (!s_screen) s_screen = heap_caps_malloc(SCREEN_W * SCREEN_H * sizeof(*s_screen), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!s_screen) s_screen = heap_caps_malloc(SCREEN_W * SCREEN_H * sizeof(*s_screen), MALLOC_CAP_8BIT);
  if (!s_screen) return;
  lcd_init();
  screen_ui_view_t view = screen_ui_view_with_phase(state, processing_phase);
  for (int i = 0; i < SCREEN_W * SCREEN_H; ++i) s_screen[i] = C_BG;
  screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 42, 13,
                            SCREEN_FONT_SMALL, "ГЕРМЕС", C_WHITE);
  screen_wifi_icon(s_wifi_connected);
  screen_rect(10, 32, 115, 1, C_LINE);
  screen_draw_icon(view, phase);
  screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 67, 155,
                            SCREEN_FONT_TITLE, view.title, C_WHITE);
  screen_hint(view.hint);
  screen_rect(45, 216, 45, 1, C_LINE);
  screen_font_draw_centered(s_screen, SCREEN_W, SCREEN_H, 67, 222,
                            SCREEN_FONT_SMALL,
                            s_screen_device_id[0] ? s_screen_device_id : "ГЕРМЕС", C_MUTED);
  int64_t render_start_us = esp_timer_get_time();
  screen_flush();
  if (!s_screen_timing_reported) {
    ESP_LOGI(TAG, "status screen full-frame time: %lld ms",
             (long long)((esp_timer_get_time() - render_start_us) / 1000));
    s_screen_timing_reported = true;
  }
  s_screen_state = state;
  s_screen_phase = phase;
  s_screen_processing_phase = processing_phase;
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
  return wifi_start_with_setup(ssid, password, true);
}

bool board_sticks3_wifi_start_ap(void) {
  return wifi_start_with_setup(NULL, NULL, false);
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
    .sample_rate = 24000, .bits_per_sample = 16, .channel_count = 1,
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
  return audio_playback_write(buf, size, 1000) == ESP_OK ? size : 0;
}
bool hw_audio_playback_drained(void) {
  return audio_playback_get_buffer_level() == 0;
}
