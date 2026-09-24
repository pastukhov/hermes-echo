# Hermes StickS3 — handoff для продолжения в ChatGPT Desktop

Дата: 2026-09-22  
Репозиторий: `/home/artem/hermes-echo`  
Устройство: M5Stack StickS3, ESP32-S3-PICO-1, 8 MB Flash + 8 MB PSRAM

## Цель

Собрать голосовой терминал Hermes на StickS3:

- удержание KEY1 начинает запись с микрофона;
- отпускание KEY1 отправляет PCM на Hermes Voice Gateway;
- ответ WAV воспроизводится через динамик;
- экран показывает состояние устройства;
- Wi‑Fi и gateway endpoint настраиваются через веб-интерфейс, как в проекте `rover_s3`.

## Что уже сделано

- Исправлена конфигурация StickS3:
  - кнопка KEY1: GPIO11;
  - KEY2: GPIO12;
  - I2C: SDA47/SCL48;
  - ST7789: SPI3, SCLK40, MOSI39, CS41, DC45, RST21, BL38;
  - LCD offsets: x=52, y=40, инверсия включена.
- Добавлено включение rails LCD/speaker через M5PM1.
- Добавлены capture/playback через ESP-IDF I2S.
- Добавлен HTTP voice transport с chunked PCM upload и потоковым WAV response.
- Добавлен инкрементальный WAV parser с проверками PCM mono16 и backpressure.
- Исправлен watchdog: главный цикл теперь делает `vTaskDelay(10 ms)`.
- Исправлена инициализация NVS перед Wi‑Fi.
- Backend Voice Gateway восстановлен и протестирован.
- Backend tests: 23 passed.
- Firmware native tests: 16/16 passed.
- StickS3 target build проходил успешно.
- Прошивка уже успешно загружалась на устройство; boot log подтвердил Flash/PSRAM/M5PM1.

## Последнее наблюдение

Синий экран — это текущий `IDLE`-индикатор. До добавления web UI прошивка не выводила текст, поэтому визуально было видно только цвет.

При первой попытке работы с Wi‑Fi serial log показал корневую ошибку:

```text
wifi osi_nvs_open fail ret=4353
```

Добавлена штатная `nvs_flash_init()`.

## Последние незавершённые изменения

Добавлен новый конфигурационный слой:

- `firmware/include/voice_settings.h`
- `firmware/src/voice_settings.c`
- `firmware/include/voice_config_httpd.h`
- `firmware/src/voice_config_httpd.c`

Он должен предоставлять:

- страницу `http://<device-ip>/`;
- поля Wi‑Fi SSID/password;
- gateway URL;
- device ID;
- device token;
- `/wifi_scan`;
- сохранение в NVS namespace `hermes`;
- reboot после сохранения.

В `main.c` добавлена загрузка настроек из NVS и запуск HTTP server. В `board_sticks3.c` добавлен fallback setup AP с SSID `Hermes-StickS3-Setup`.

## Важная текущая точка

После добавления web UI target build уже прошёл, native tests тоже прошли, но именно последняя версия web UI ещё не была загружена на StickS3 и проверена по HTTP.

Следующий обязательный цикл:

1. Пересобрать приватную прошивку с параметрами через environment variables.
2. Загрузить её на `/dev/ttyACM0`.
3. Снять boot log.
4. Проверить, что нет reset/watchdog.
5. Проверить STA Wi‑Fi и открыть `http://<полученный-IP>/`.
6. Проверить `/config` и `/wifi_scan`.
7. Проверить сохранение формы и reboot.

## Сборка с приватными параметрами

Пароли и токены нельзя коммитить или помещать в Markdown. `platformio.ini` использует переменные окружения:

```sh
export HERMES_WIFI_SSID='...'
export HERMES_WIFI_PASSWORD='...'
export HERMES_GATEWAY_URL='http://<gateway-host>:8080/api/v1/voice/turn'
export HERMES_DEVICE_ID='sticks3-01'
/home/artem/platformio/.venv/bin/platformio run -d firmware -e sticks3
```

Загрузка:

```sh
/home/artem/platformio/.venv/bin/platformio run \
  -d firmware -e sticks3 -t upload --upload-port /dev/ttyACM0
```

Если порт исчезает, удерживать KEY1 при переподключении USB, пока StickS3 не появится в download mode.

## Serial log

```sh
/home/artem/platformio/.venv/bin/python - <<'PY'
import serial, time
p = serial.Serial('/dev/ttyACM0', 115200, timeout=.2)
p.dtr = False
p.rts = True
time.sleep(.15)
p.rts = False
end = time.time() + 12
while time.time() < end:
    data = p.read(2048)
    if data:
        print(data.decode('utf-8', 'replace'), end='')
p.close()
PY
```

Ожидаемые признаки:

```text
sticks3: Flash detected: 8388608 bytes
sticks3: PSRAM total: 8388608 bytes
sticks3: M5PM1 LCD and speaker rails enabled
voice_httpd: settings UI started on port 80
```

Для настроенной сети также нужен лог `Wi-Fi got IP: ...`.

## Риски, которые нужно проверить

- `voice_config_httpd.c` — новый код, проверить компиляцию target и HTTP runtime.
- В `h_config_get` пароль намеренно не возвращается в JSON; поле формы остаётся пустым.
- POST сейчас использует `application/x-www-form-urlencoded` и перезапускает устройство после сохранения.
- Setup AP пока без captive DNS; открыть вручную `http://192.168.4.1/`.
- При уже сохранённом Wi‑Fi устройство стартует в STA, web UI доступен по DHCP IP.
- HTTP server использует порт 80; gateway остаётся отдельным endpoint из настройки.
- Не выводить пароль Wi‑Fi или device token в serial log, Markdown и финальные сообщения.

## Полезные файлы

- [Руководство по прошивке StickS3](flash-sticks3.md)
- [Flash guide](flash-sticks3.md)
- [Voice terminal context](voice-terminal-context.md)
- [Progress log](../.superpowers/sdd/2026-09-22-sticks3-hermes-terminal/progress.md)
- Reference web UI: `/home/artem/repos/esp-claw/application/rover_s3/main/rover_s3_httpd.c`
- Reference Wi‑Fi/AP: `/home/artem/repos/esp-claw/application/rover_s3/main/rover_s3_wifi.c`
