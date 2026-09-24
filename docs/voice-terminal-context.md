# Hermes Voice Terminal: собранный контекст

Дата фиксации: 2026-09-22.

## Цель

Собрать голосовой терминал на M5Stack StickS3: KEY1 запускает запись, устройство
передаёт PCM на Voice Gateway, gateway выполняет STT → Hermes → заметки → TTS,
а StickS3 воспроизводит WAV-ответ через ES8311/AW8737.

## Источники

- `ТЗ_ Hermes Voice Terminal на M5Stack ATOM Echo.md` — актуальное содержимое
  уже исправлено под StickS3, имя файла историческое.
- `docs/architecture.md`, `docs/protocol.md`, `docs/development.md` — текущий
  backend-контракт и правила разработки.
- `firmware/` — незавершённая переносимая прошивка с host-тестами.
- `/home/artem/repos/esp-claw/application/rover_s3` — рабочий StickS3 shell:
  дисплей, M5PM1, Wi-Fi, PlatformIO/ESP-IDF конфигурация.
- `/home/artem/repos/esp-claw/components/claw_capabilities/cap_voice` — рабочий
  пример I2S + ES8311 через `esp_codec_dev`, запись и воспроизведение.

## Аппарат StickS3

- ESP32-S3-PICO-1-N8R8, 8 MB Flash, 8 MB Octal PSRAM.
- ES8311 mono codec, MEMS microphone, AW8737 amplifier.
- I2S: MCLK GPIO18, BCLK GPIO17, LRCK GPIO15, DOUT GPIO14, DIN GPIO16.
- Codec/PMU I2C: SDA GPIO47, SCL GPIO48; ES8311 address is represented as
  `0x30` in the `esp_codec_dev` example (the driver shifts it as needed).
- KEY1 GPIO11, active low; KEY2 GPIO12, outside MVP.
- ST7789P3 135×240: MOSI GPIO39, SCK GPIO40, RS/DC GPIO45, CS GPIO41,
  RST GPIO21, backlight GPIO38.
- M5PM1 controls LCD rail and speaker PA; its I2C address is `0x6e`.

## Current state

### Backend

The FastAPI service exposes `POST /api/v1/voice/turn`, accepts raw S16LE PCM,
archives each turn, and has stage abstractions for STT, Hermes, notes and TTS.
The response contract is `audio/wav` plus `X-Turn-Id`; device authentication is
configurable through a bearer token or `X-Device-Token`.

The backend now imports cleanly and its stream suite passes. The configured
STT → Hermes → TTS path is gated by a non-empty valid WAV response; when TTS is
not injected, the legacy ingest-only 200 response remains explicit. The
firmware plan still requires an actual `esp_http_client` request/response proof
rather than only a host fake.

### Firmware

`firmware/src/main.c` contains a host-testable state machine and a Milestone-1
local loopback design. `board_sticks3.c` is an incomplete hand-written port. The
current PlatformIO file still targets `m5stack-atom`; the active firmware does
not compile because of stale `board_atom_echo` names, mismatched playback APIs,
and missing `hw_audio_playback_drained`.

The target now has a buildable `sticks3` environment, an on-target `app_main`,
corrected StickS3 GPIO aliases, Wi-Fi STA bootstrap, and a bounded transport
seam. HTTP response chunks are parsed incrementally and fed into the playback
queue only after a successful 200 response. Physical flash/PSRAM, display,
codec and network validation remain outstanding.

### Rover reference

`rover_s3` already demonstrates the correct board initialization and display
configuration. `cap_voice_audio.c` demonstrates the correct ES8311 lifecycle:
shared I2S channels, 16 kHz capture, closing/reopening the codec for 24 kHz
playback, and non-zero speaker volume. These are reference implementations, not
files to compile into Hermes unchanged.

## Kanban status at freeze

The `voice-terminal` board contained 178 cards: 112 done, 16 todo, 7 blocked,
1 review, 4 triage, 39 archived. Several todo cards were gated by unfinished or
blocked parents. The main gateway configuration now has
`kanban.dispatch_in_gateway: false`; the gateway was restarted, so automatic
task dispatch is paused. The board and its history remain intact.

## Known risks

1. ES8311 and M5PM1 share the I2C bus; initialization order must avoid creating
   conflicting bus handles.
2. ES8311 capture and playback share an I2S clock; rate changes must close one
   codec direction before opening the other.
3. Battery operation requires conservative speaker volume (StickS3 documentation
   recommends below 75%).
4. The existing checkout is dirty from earlier task work. New changes must not
   erase unrelated user changes or generated artifacts.
5. Physical validation is required for audio, display orientation, Wi-Fi and
   USB flashing; host tests cannot prove these.
