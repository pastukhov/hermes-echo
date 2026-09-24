# StickS3 voice recorder + AI voice front-end (UIFlow2 / MicroPython)
#
# Mode DICT : hold BtnA to record a note, tap BtnA to play/pause it,
#             tap BtnB for the next note, hold BtnB to switch mode.
# Mode AI   : hold BtnA to ask a question out loud, tap BtnA/BtnB to replay
#             the spoken answer, hold BtnB to switch back.
#
# The AI mode talks to any OpenAI-compatible HTTP API. The endpoints, keys and
# model names live in /flash/res/config/ai.txt (see res/config/ai.txt).

import os
import time
import json
import M5
from M5 import *
from audio import Recorder
from audio import Player

try:
    import requests2

    HAVE_HTTP = True
except ImportError:
    requests2 = None
    HAVE_HTTP = False

try:
    import network

    HAVE_NET = True
except ImportError:
    network = None
    HAVE_NET = False


FLASH = "/flash"
AUDIO_DIR = "/res/audio"
CFG_PATH = "/res/config/ai.txt"
REPLY_FILE = AUDIO_DIR + "/reply.mp3"

BG = 0x0B1017
PANEL = 0x161F2B
BAR_BG = 0x1B2430
BAR_EDGE = 0x39506B
C_TITLE = 0x2FD8E8
C_TEXT = 0xE8F1F8
C_DIM = 0x8FA3B5
C_REC = 0xFF4D5A
C_OK = 0x3DDC84
C_ERR = 0xFF6B6B

SAMPLE_RATE = 8000
BITS = 16
STEREO = True
CHANNELS = 2 if STEREO else 1
BYTES_PER_SEC = SAMPLE_RATE * (BITS // 8) * CHANNELS
MAX_NOTE_S = 20
MAX_ASK_S = 8
MIN_REC_S = 1
MAX_NOTES = 6
VOL_PCT = 70
HTTP_TIMEOUT = 20

MODE_DICT = 0
MODE_AI = 1

SW = 240
SH = 135
HOLD_START_MS = 250
MODE_HOLD_MS = 900

HEADER_H = 18
STATUS_Y = 22
STATUS_H = 30
BAR_X = 6
BAR_Y = 56
BAR_W = 228
BAR_H = 10
LINE1_Y = 70
LINE2_Y = 88
LINE3_Y = 106
HINT_Y = 122
LINE_H1 = 16
LINE_H2 = 16
HINT_W = 228

DEFAULT_SYSTEM = (
    "You are a helpful voice assistant on a small handheld device. "
    "Answer in at most two short sentences."
)

recorder = None
player = None

mode = MODE_DICT
status = "READY"
err_msg = ""
err_until = 0
ai_q = ""
ai_a = ""

recording = False
rec_path = ""
rec_start_ms = 0
rec_max_s = 0

playing = False
play_path = ""
play_start_ms = 0
play_last_pos = -1
play_last_change = 0
play_probed = False
play_form = 0

a_down = False
a_down_ms = 0
a_hold_started = False
b_down = False
b_down_ms = 0
b_hold_done = False

notes = []
cur_idx = 0
free_kb = -1
last_draw_ms = 0
last_free_ms = 0

cfg = {}
timeout_ok = True

f_small = None
f_body = None
f_big = None


def pick_font(name, fallback):
    f = getattr(M5.Lcd.FONTS, name, None)
    if f is None:
        f = getattr(M5.Lcd.FONTS, fallback, None)
    return f


def txt(x, y, s, color, f, w, h, bg=BG):
    if w > 0:
        M5.Lcd.fillRect(x, y, w, h, bg)
    if f is not None:
        try:
            M5.Lcd.setFont(f)
        except Exception:
            pass
    M5.Lcd.setTextColor(color, bg)
    M5.Lcd.setCursor(x, y)
    M5.Lcd.print(s, color)


def shorten(s, n):
    s = str(s)
    if len(s) <= n:
        return s
    return s[: n - 2] + ".."


def wrap_two(s, n):
    words = str(s).split()
    line1 = ""
    line2 = ""
    for w in words:
        if len(line1) + len(w) + 1 <= n:
            line1 = w if line1 == "" else line1 + " " + w
        elif len(line2) + len(w) + 1 <= n:
            line2 = w if line2 == "" else line2 + " " + w
        else:
            line2 = shorten(line2 + " " + w, n)
            break
    return line1, line2


def fmt_time(ms):
    if ms < 0:
        ms = 0
    t = ms // 1000
    return "%02d:%02d" % (t // 60, t % 60)


def free_bytes():
    try:
        st = os.statvfs(FLASH)
        return st[1] * st[4]
    except Exception:
        return -1


def ensure_dirs():
    for d in ("/res", AUDIO_DIR, "/res/config"):
        try:
            os.mkdir(FLASH + d)
        except Exception:
            pass


def note_files():
    out = []
    try:
        names = os.listdir(FLASH + AUDIO_DIR)
    except Exception:
        return out
    for n in names:
        if n.startswith("note_") and n.endswith(".wav"):
            out.append(n)
    out.sort()
    return out


def note_path(name):
    return AUDIO_DIR + "/" + name


def refresh_notes():
    global notes, cur_idx
    notes = note_files()
    if cur_idx >= len(notes):
        cur_idx = len(notes) - 1
    if cur_idx < 0:
        cur_idx = 0


def ensure_space(need):
    for _ in range(MAX_NOTES + 2):
        free = free_bytes()
        if free < 0 or free >= need:
            return True
        files = note_files()
        if not files:
            return False
        try:
            os.remove(FLASH + AUDIO_DIR + "/" + files[0])
        except Exception:
            return False
        refresh_notes()
    return free_bytes() < 0


def file_size(path):
    try:
        return os.stat(path)[6]
    except Exception:
        return 0


def wav_info(path):
    size = file_size(path)
    dur_ms = 0
    try:
        f = open(path, "rb")
        head = f.read(44)
        f.close()
        if len(head) == 44 and head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
            byte_rate = int.from_bytes(head[28:32], "little")
            data_size = int.from_bytes(head[40:44], "little")
            if byte_rate > 0 and data_size > 0:
                dur_ms = (data_size * 1000) // byte_rate
    except Exception:
        pass
    return size, dur_ms


def fix_wav(path):
    # Recorder.stop() can leave the RIFF and data chunk sizes at the value of
    # the intended duration. Rewrite them from the real file size.
    size = file_size(path)
    if size < 44:
        return size
    try:
        f = open(path, "r+b")
    except Exception:
        return size
    try:
        head = f.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            f.close()
            return size
        pos = 12
        guard = 0
        while pos + 8 <= size and guard < 16:
            guard += 1
            f.seek(pos)
            cid = f.read(4)
            csz = int.from_bytes(f.read(4), "little")
            if cid == b"data":
                f.seek(pos + 4)
                f.write((size - (pos + 8)).to_bytes(4, "little"))
                f.seek(4)
                f.write((size - 8).to_bytes(4, "little"))
                break
            if csz <= 0:
                break
            pos += 8 + csz + (csz & 1)
        f.close()
    except Exception:
        try:
            f.close()
        except Exception:
            pass
    return size


def audio_init():
    global recorder, player
    for step in ("mic_end", "spk_end", "spk_pa"):
        try:
            if step == "mic_end":
                Mic.end()
            elif step == "spk_end":
                Speaker.end()
            else:
                Speaker.setPA(True)
        except Exception:
            pass
    try:
        recorder = Recorder(SAMPLE_RATE, BITS, STEREO)
    except Exception:
        recorder = None
    try:
        player = Player(None)
        player.set_vol(VOL_PCT)
    except Exception:
        player = None


def cue(freq, ms):
    if player is None:
        return
    try:
        player.play_tone(freq, ms / 1000.0, VOL_PCT, True)
    except Exception:
        pass


def start_capture(now):
    global recording, rec_path, rec_start_ms, rec_max_s, status
    if recording or playing or recorder is None:
        return
    ensure_dirs()
    if mode == MODE_AI:
        if not ai_ready():
            set_err("NO AI CONFIG")
            return
        if not net_ok():
            set_err("WIFI OFFLINE")
            return
        path = AUDIO_DIR + "/ask.wav"
        max_s = MAX_ASK_S
    else:
        files = note_files()
        if len(files) >= MAX_NOTES:
            try:
                os.remove(FLASH + AUDIO_DIR + "/" + files[0])
            except Exception:
                pass
            files = note_files()
        n = 1
        while n < 1000:
            name = "note_%03d.wav" % n
            if name not in files:
                break
            n += 1
        path = AUDIO_DIR + "/" + name
        max_s = MAX_NOTE_S
        if not ensure_space(BYTES_PER_SEC * max_s + 16384):
            set_err("FLASH FULL")
            return
    cue(1400, 60)
    try:
        recorder.record("file://flash" + path, max_s, False)
    except Exception as e:
        set_err("REC ERR " + shorten(e, 20))
        return
    recording = True
    rec_path = path
    rec_start_ms = now
    rec_max_s = max_s
    status = "RECORDING"


def stop_capture(now):
    global recording, status, cur_idx
    if not recording:
        return
    recording = False
    held_ms = time.ticks_diff(now, rec_start_ms)
    try:
        recorder.stop()
    except Exception:
        pass
    waited = 0
    while waited < 2000:
        try:
            if not recorder.is_recording():
                break
        except Exception:
            break
        M5.update()
        time.sleep_ms(40)
        waited += 40
    path = FLASH + rec_path
    size = fix_wav(path)
    if held_ms < MIN_REC_S * 1000 or size < 4096:
        try:
            os.remove(path)
        except Exception:
            pass
        set_err("TOO SHORT")
        status = "READY"
        return
    cue(900, 70)
    if mode == MODE_AI:
        run_ai_query(rec_path)
    else:
        refresh_notes()
        name = rec_path.split("/")[-1]
        if name in notes:
            cur_idx = notes.index(name)
        status = "READY"


def play_uri(path, form):
    if form == 0:
        return "file://flash" + path
    return "file:///flash" + path


def start_play(path):
    global playing, play_path, play_start_ms, play_last_pos, play_last_change
    global play_probed, play_form, status
    if player is None or path == "":
        return
    size = file_size(FLASH + path)
    if size < 32:
        set_err("NO AUDIO DATA")
        return
    if playing:
        stop_play()
    play_form = 0
    try:
        player.play(play_uri(path, play_form), pos=0, volume=-1, sync=False)
    except Exception as e:
        set_err("PLAY ERR " + shorten(e, 18))
        return
    playing = True
    play_path = path
    play_start_ms = time.ticks_ms()
    play_last_pos = -1
    play_last_change = play_start_ms
    play_probed = False
    status = "PLAYING"


def stop_play():
    global playing, status
    if not playing:
        return
    playing = False
    try:
        player.stop()
    except Exception:
        pass
    status = "READY"


def poll_play(now):
    global playing, play_last_pos, play_last_change, play_probed, play_form
    global play_start_ms, status
    if not playing:
        return
    size = file_size(FLASH + play_path)
    try:
        pos = player.pos()
    except Exception:
        pos = -1
    elapsed = time.ticks_diff(now, play_start_ms)
    if pos is not None and pos > play_last_pos:
        play_last_pos = pos
        play_last_change = now
        if size > 0 and pos >= size - 128:
            stop_play()
            return
    # Nothing progressed yet: one of the two URI slash forms is silently wrong,
    # so try the other one before trusting any end-of-file estimate.
    if (not play_probed) and elapsed > 900 and play_last_pos <= 0:
        play_probed = True
        play_form = 1
        try:
            player.play(play_uri(play_path, play_form), pos=0, volume=-1, sync=False)
            play_start_ms = now
            play_last_change = now
        except Exception:
            pass
        return
    if play_last_pos > 0:
        size2, dur_ms = wav_info(FLASH + play_path)
        if dur_ms > 0 and elapsed > dur_ms + 600:
            stop_play()
            return
        if time.ticks_diff(now, play_last_change) > 1500:
            stop_play()
            return
    elif elapsed > 3500:
        stop_play()
        set_err("PLAY SILENT")
        return
    if elapsed > 120000:
        stop_play()


def load_cfg():
    global cfg
    cfg = {}
    try:
        f = open(FLASH + CFG_PATH)
    except Exception:
        return
    try:
        for line in f:
            line = line.strip()
            if line == "" or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except Exception:
        pass
    try:
        f.close()
    except Exception:
        pass


def cfg_get(key, default=""):
    v = cfg.get(key, default)
    if v is None:
        return default
    v = v.strip()
    if v == "" or v.startswith("YOUR_"):
        return default
    return v


def ai_ready():
    if not HAVE_HTTP:
        return False
    for k in ("STT_URL", "STT_MODEL", "CHAT_URL", "CHAT_MODEL"):
        if cfg_get(k) == "":
            return False
    return True


def net_ok():
    if not HAVE_NET:
        return False
    try:
        return network.WLAN(network.STA_IF).isconnected()
    except Exception:
        return False


def http_post(url, data=None, jobj=None, headers=None):
    global timeout_ok
    kw = {}
    if data is not None:
        kw["data"] = data
    if jobj is not None:
        kw["json"] = jobj
    if headers is not None:
        kw["headers"] = headers
    if timeout_ok:
        try:
            return requests2.post(url, timeout=HTTP_TIMEOUT, **kw)
        except TypeError:
            timeout_ok = False
    return requests2.post(url, **kw)


def body_head(raw):
    # Only decode a short slice: requests2 .text re-decodes the whole body and
    # would hold a second full-size copy of it in the heap.
    try:
        if isinstance(raw, (bytes, bytearray)):
            return raw[:48].decode("utf-8", "ignore").replace("\n", " ")
        return str(raw)[:48]
    except Exception:
        return ""


def bearer(key):
    h = {"Content-Type": "application/json"}
    if key != "":
        h["Authorization"] = "Bearer " + key
    return h


def stt_text(path):
    boundary = "----m5sticks3voice"
    pre = (
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="file"; filename="q.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode()
    post = (
        "\r\n--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="model"\r\n\r\n'
        + cfg_get("STT_MODEL")
        + "\r\n"
        "--" + boundary + "--\r\n"
    ).encode()
    body = bytearray(pre)
    f = open(FLASH + path, "rb")
    while True:
        chunk = f.read(2048)
        if not chunk:
            break
        body.extend(chunk)
    f.close()
    body.extend(post)
    r = http_post(
        cfg_get("STT_URL"),
        data=bytes(body),
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Authorization": "Bearer " + cfg_get("STT_KEY"),
        },
    )
    code = r.status_code
    raw = r.content
    try:
        r.close()
    except Exception:
        pass
    if code != 200:
        raise OSError("STT " + str(code) + " " + body_head(raw))
    d = json.loads(raw)
    t = d.get("text", "")
    if t == "":
        raise OSError("STT EMPTY")
    return t.strip()


def chat_reply(text):
    sysmsg = cfg_get("SYSTEM_PROMPT")
    if sysmsg == "":
        sysmsg = DEFAULT_SYSTEM
    body = {
        "model": cfg_get("CHAT_MODEL"),
        "messages": [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": text},
        ],
        "max_tokens": 300,
    }
    r = http_post(cfg_get("CHAT_URL"), jobj=body, headers=bearer(cfg_get("CHAT_KEY")))
    code = r.status_code
    raw = r.content
    try:
        r.close()
    except Exception:
        pass
    if code != 200:
        raise OSError("CHAT " + str(code) + " " + body_head(raw))
    d = json.loads(raw)
    return d["choices"][0]["message"]["content"].strip()


def tts_save(text, out_path):
    url = cfg_get("TTS_URL")
    if url == "":
        return False
    body = {
        "model": cfg_get("TTS_MODEL"),
        "input": text,
        "voice": cfg_get("TTS_VOICE"),
        "response_format": "mp3",
    }
    r = http_post(url, jobj=body, headers=bearer(cfg_get("TTS_KEY")))
    code = r.status_code
    data = r.content
    try:
        r.close()
    except Exception:
        pass
    if code != 200 or not data:
        raise OSError("TTS " + str(code))
    f = open(FLASH + out_path, "wb")
    f.write(data)
    f.close()
    return True


def run_ai_query(path):
    global status, ai_q, ai_a, err_until
    status = "UPLOADING"
    draw(True)
    try:
        question = stt_text(path)
    except Exception as e:
        set_err("STT: " + shorten(e, 26))
        status = "READY"
        return
    ai_q = question
    status = "THINKING"
    draw(True)
    try:
        answer = chat_reply(question)
    except Exception as e:
        set_err("CHAT: " + shorten(e, 25))
        status = "READY"
        return
    ai_a = answer
    status = "ANSWER"
    draw(True)
    try:
        if tts_save(answer, REPLY_FILE):
            status = "SPEAKING"
            start_play(REPLY_FILE)
    except Exception as e:
        set_err("TTS: " + shorten(e, 26))
    if status == "ANSWER":
        status = "READY"


def set_err(msg):
    global err_msg, err_until
    err_msg = str(msg)
    err_until = time.ticks_ms() + 5000
    draw(True)


def toggle_mode():
    global mode, status, ai_q, ai_a
    if recording or playing:
        return
    if mode == MODE_DICT:
        mode = MODE_AI
        load_cfg()
        status = "READY"
    else:
        mode = MODE_DICT
        status = "READY"
    cue(1800, 60)
    draw(True)


def on_tap(now):
    if mode == MODE_AI:
        if file_size(FLASH + REPLY_FILE) > 44:
            if playing:
                stop_play()
            else:
                start_play(REPLY_FILE)
        else:
            set_err("NO ANSWER YET")
        return
    refresh_notes()
    if not notes:
        set_err("NO NOTES")
        return
    if playing:
        stop_play()
        return
    start_play(note_path(notes[cur_idx]))


def on_b_click(now):
    global cur_idx
    if playing:
        stop_play()
        return
    if mode == MODE_AI:
        on_tap(now)
        return
    refresh_notes()
    if not notes:
        set_err("NO NOTES")
        return
    cur_idx = (cur_idx + 1) % len(notes)
    status = "READY"
    draw(True)


def handle_a(now):
    global a_down, a_down_ms, a_hold_started
    if BtnA.isPressed():
        if not a_down:
            a_down = True
            a_down_ms = now
            a_hold_started = False
        elif (not a_hold_started) and (not recording) and (not playing):
            if time.ticks_diff(now, a_down_ms) >= HOLD_START_MS:
                a_hold_started = True
                start_capture(now)
    else:
        if a_down:
            a_down = False
            held = a_hold_started
            a_hold_started = False
            if recording:
                stop_capture(now)
            elif not held:
                on_tap(now)


def handle_b(now):
    global b_down, b_down_ms, b_hold_done
    if BtnB.isPressed():
        if not b_down:
            b_down = True
            b_down_ms = now
            b_hold_done = False
        elif (not b_hold_done) and time.ticks_diff(now, b_down_ms) >= MODE_HOLD_MS:
            b_hold_done = True
            toggle_mode()
    else:
        if b_down:
            b_down = False
            if not b_hold_done:
                on_b_click(now)
            b_hold_done = False


def draw_bar(frac, color):
    if frac < 0.0:
        frac = 0.0
    if frac > 1.0:
        frac = 1.0
    M5.Lcd.fillRect(BAR_X, BAR_Y, BAR_W, BAR_H, BAR_BG)
    w = int(BAR_W * frac)
    if w > 0:
        M5.Lcd.fillRect(BAR_X, BAR_Y, w, BAR_H, color)
    M5.Lcd.drawRect(BAR_X, BAR_Y, BAR_W, BAR_H, BAR_EDGE)


def draw(force):
    global last_draw_ms, last_free_ms, free_kb, status
    now = time.ticks_ms()
    if (not force) and time.ticks_diff(now, last_draw_ms) < 120:
        return
    last_draw_ms = now

    if time.ticks_diff(now, last_free_ms) > 1500 or free_kb < 0:
        last_free_ms = now
        b = free_bytes()
        free_kb = -1 if b < 0 else b // 1024
    free_s = "FREE ---" if free_kb < 0 else "FREE %dK" % free_kb
    head = "AI VOICE" if mode == MODE_AI else "DICTAPHONE"
    M5.Lcd.fillRect(0, 0, SW, HEADER_H, PANEL)
    txt(6, 3, head, C_TITLE, f_small, 0, HEADER_H, PANEL)
    txt(150, 3, free_s, C_DIM, f_small, 84, HEADER_H, PANEL)

    if recording:
        elapsed = time.ticks_diff(now, rec_start_ms)
        st = "REC " + fmt_time(elapsed)
        st_col = C_REC
    elif playing:
        elapsed = time.ticks_diff(now, play_start_ms)
        st = "PLAY " + fmt_time(elapsed)
        st_col = C_OK
    elif status == "READY":
        st = "READY"
        st_col = C_TEXT
    else:
        st = status
        st_col = C_TITLE
    txt(6, STATUS_Y, shorten(st, 13), st_col, f_big, 152, STATUS_H)
    if mode == MODE_AI:
        info = "AI"
    else:
        if notes:
            info = "%d/%d" % (cur_idx + 1, len(notes))
        else:
            info = "--"
    txt(170, STATUS_Y + 6, info, C_DIM, f_body, 64, 20)

    if recording and recorder is not None:
        vol = -1
        try:
            if time.ticks_diff(now, rec_start_ms) > 200:
                vol = recorder.volume()
        except Exception:
            vol = -1
        if vol is None or vol < 0:
            vol = 0
        draw_bar(vol / 100.0, C_REC)
    elif playing:
        size, dur_ms = wav_info(FLASH + play_path)
        frac = 0.0
        if dur_ms > 0:
            frac = float(time.ticks_diff(now, play_start_ms)) / float(dur_ms)
        elif size > 0:
            frac = float(play_last_pos) / float(size)
        draw_bar(frac, C_OK)
    else:
        draw_bar(0.0, BAR_BG)

    if mode == MODE_AI:
        if ai_q == "":
            txt(6, LINE1_Y, "Hold A: ask a question", C_TEXT, f_body, 228, LINE_H1)
        else:
            txt(6, LINE1_Y, "YOU: " + shorten(ai_q, 30), C_TEXT, f_body, 228, LINE_H1)
        if ai_a == "":
            if not ai_ready():
                txt(
                    6,
                    LINE2_Y,
                    "AI: no config (res/config/ai.txt)",
                    C_ERR,
                    f_body,
                    228,
                    LINE_H2,
                )
            elif not net_ok():
                txt(6, LINE2_Y, "AI: wifi offline", C_ERR, f_body, 228, LINE_H2)
            else:
                txt(6, LINE2_Y, "AI: ready", C_OK, f_body, 228, LINE_H2)
        else:
            l1, l2 = wrap_two(ai_a, 32)
            txt(6, LINE2_Y, "AI: " + l1, C_OK, f_body, 228, LINE_H2)
            txt(6, LINE3_Y, l2, C_TEXT, f_body, 228, LINE_H2)
    else:
        if notes:
            cur = notes[cur_idx]
            size, dur_ms = wav_info(FLASH + note_path(cur))
            txt(
                6,
                LINE1_Y,
                shorten(cur, 14)
                + "  "
                + fmt_time(dur_ms)
                + "  "
                + str(size // 1024)
                + "K",
                C_TEXT,
                f_body,
                228,
                LINE_H1,
            )
        else:
            txt(6, LINE1_Y, "no notes yet", C_DIM, f_body, 228, LINE_H1)
        txt(6, LINE2_Y, "hold A: record   tap A: play", C_DIM, f_body, 228, LINE_H2)

    if time.ticks_diff(now, err_until) < 0 and err_msg != "":
        txt(6, LINE3_Y, "! " + shorten(err_msg, 32), C_ERR, f_body, 228, LINE_H2)
    elif mode == MODE_DICT:
        txt(6, LINE3_Y, "", C_DIM, f_body, 228, LINE_H2)

    if playing:
        hint = "tap B: stop"
    elif mode == MODE_DICT:
        hint = "tap B: next note   hold B: AI mode"
    else:
        hint = "tap B: replay answer   hold B: dict mode"
    txt(6, HINT_Y, shorten(hint, 40), C_DIM, f_small, HINT_W, 13)


def setup():
    global f_small, f_body, f_big
    M5.begin()
    M5.Lcd.setRotation(1)
    M5.Lcd.fillScreen(BG)
    f_small = pick_font("Montserrat12", "Montserrat14")
    f_body = pick_font("Montserrat14", "Montserrat12")
    f_big = pick_font("Montserrat24", "Montserrat18")
    ensure_dirs()
    audio_init()
    load_cfg()
    refresh_notes()
    if not HAVE_HTTP:
        set_err("NO HTTP MODULE")
    draw(True)


def loop():
    M5.update()
    now = time.ticks_ms()
    handle_a(now)
    handle_b(now)
    if recording and time.ticks_diff(now, rec_start_ms) > rec_max_s * 1000 + 400:
        stop_capture(now)
    poll_play(now)
    draw(False)
    time.sleep_ms(20)


if __name__ == "__main__":
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            from utility import print_error_msg

            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")
