"""
Sky Game Chat Translator v2
- F9: capture + OCR (rus) + translate (ru->en) + show in overlay
- ESC: pause/resume (keeps model loaded; avoids restart delay)
- F10: toggle overlay visibility
- F8: quit cleanly
"""

import sys
import time
import threading
import queue
import re
from collections import deque
from datetime import datetime

from PIL import Image, ImageGrab, ImageEnhance, ImageOps
import pytesseract
from pytesseract import Output
from transformers import MarianMTModel, MarianTokenizer
import keyboard
import torch

try:
    import tkinter as tk
except Exception:  # pragma: no cover (environment-specific)
    tk = None

try:
    import mss  # type: ignore
except Exception:  # pragma: no cover
    mss = None


# -------------------------
# Configuration
# -------------------------
CAPTURE_HOTKEY = "f9"
PAUSE_TOGGLE_HOTKEY = "esc"
OVERLAY_TOGGLE_HOTKEY = "f10"
QUIT_HOTKEY = "f8"  # Change to "f11" if you prefer

OVERLAY_ENABLED = True
OVERLAY_ALPHA = 0.86
OVERLAY_MAX_LINES = 8
OVERLAY_MARGIN_PX = 16
OVERLAY_WIDTH_PX = 520
OVERLAY_HEIGHT_PX = 260
OVERLAY_WRAP_PX = 500
OVERLAY_SHOW_RU = False
OVERLAY_SHOW_TIMESTAMP = False
OVERLAY_FONT = ("Segoe UI", 12)
OVERLAY_STATUS_FONT = ("Segoe UI", 11, "bold")

SEEN_CACHE_MAX = 120  # bounds memory + prevents infinite duplicate suppression

USE_GPU_IF_AVAILABLE = True
USE_FP16_IF_CUDA = True

TRANSLATE_NUM_BEAMS = 2  # Balanced default
MAX_SOURCE_TOKENS = 128
MAX_NEW_TOKENS = 128

USE_MSS = True  # faster screen capture than ImageGrab on many Windows setups
OCR_DOWNSCALE = 1.0  # try 0.75 for speed (can reduce accuracy)

MIN_OCR_CONF = 45
MIN_CYRILLIC_LETTERS = 4
MIN_MSG_CHARS = 3

IGNORE_SPEAKERS = {"anonymous"}  # hidden players show as "... - Anonymous"

_PUNCT_DOT_RE = re.compile(r"[.\u00b7\u2026\u2022\u2219\-_—–\s]+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _normalize_key(s: str) -> str:
    return _norm_space(s).lower()


def _cyrillic_stats(s: str):
    total_letters = sum(ch.isalpha() for ch in s)
    cyr = len(_CYRILLIC_RE.findall(s))
    ratio = (cyr / total_letters) if total_letters > 0 else 0.0
    return cyr, ratio


def _looks_like_dots_or_junk(s: str) -> bool:
    stripped = _PUNCT_DOT_RE.sub("", s)
    stripped = re.sub(r"\d+", "", stripped)
    return len(stripped) < MIN_MSG_CHARS


def _split_speaker(line: str):
    if " - " in line:
        msg, speaker = line.rsplit(" - ", 1)
        msg = _norm_space(msg)
        speaker = _norm_space(speaker)
        if speaker:
            return msg, speaker
    return _norm_space(line), ""


class OverlayWindow:
    def __init__(self, chat_region):
        if tk is None:
            raise RuntimeError("tkinter is not available in this Python environment.")

        self.chat_region = chat_region
        self.visible = True
        self.paused = False
        self.lines = deque(maxlen=OVERLAY_MAX_LINES)
        self._base_status = ""
        self._stage = ""

        self.root = tk.Tk()
        self.root.title("Sky Translator Overlay")
        self.root.overrideredirect(True)  # borderless
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(OVERLAY_ALPHA))

        # Make it look intentional and readable on top of the game.
        self.root.configure(bg="#0b0f14")

        self.status_var = tk.StringVar(value="")
        self.text_var = tk.StringVar(value="")

        self.status_label = tk.Label(
            self.root,
            textvariable=self.status_var,
            font=OVERLAY_STATUS_FONT,
            fg="#e6e6e6",
            bg="#0b0f14",
            padx=10,
            pady=6,
            anchor="w",
        )
        self.status_label.pack(fill="x")

        self.text_label = tk.Label(
            self.root,
            textvariable=self.text_var,
            font=OVERLAY_FONT,
            fg="#d7f7d7",
            bg="#0b0f14",
            justify="left",
            wraplength=int(OVERLAY_WRAP_PX),
            padx=10,
            pady=10,
            anchor="nw",
        )
        self.text_label.pack(fill="both", expand=True)

        # Initial placement: just to the right of the chat region.
        x = int(self.chat_region[2] + OVERLAY_MARGIN_PX)
        y = int(self.chat_region[1])
        self.root.geometry(f"{int(OVERLAY_WIDTH_PX)}x{int(OVERLAY_HEIGHT_PX)}+{x}+{y}")

        # Drag to reposition (simple + reliable).
        self._drag_x = 0
        self._drag_y = 0
        self.root.bind("<ButtonPress-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_move)

        self.set_paused(False)

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_move(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def set_paused(self, paused: bool):
        self.paused = paused
        if paused:
            self._base_status = "PAUSED | F9 resume+capture  ESC resume  F10 hide  F8 quit"
        else:
            self._base_status = "RUNNING | F9 capture  ESC pause  F10 hide  F8 quit"
        self._refresh_status()

    def set_stage(self, stage: str):
        self._stage = _norm_space(stage)
        self._refresh_status()

        # Auto-clear terminal-ish stages so the UI doesn't get stuck.
        if (
            self._stage.startswith("Done")
            or self._stage.startswith("No Russian")
            or self._stage.startswith("Busy")
            or self._stage.startswith("Queued")
        ):
            current = self._stage

            def _clear_if_unchanged():
                if self._stage == current:
                    self._stage = ""
                    self._refresh_status()

            self.root.after(1400, _clear_if_unchanged)

    def _refresh_status(self):
        if self._stage:
            self.status_var.set(f"{self._base_status}  |  {self._stage}")
        else:
            self.status_var.set(self._base_status)

    def toggle_visibility(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False
        else:
            self.root.deiconify()
            self.visible = True

    def push_translations(self, timestamp: str, translations):
        # translations: list[{"ru": str, "en": str}]
        for item in translations:
            if OVERLAY_SHOW_RU:
                if OVERLAY_SHOW_TIMESTAMP:
                    line = f"[{timestamp}] RU: {item['ru']}\n[{timestamp}] EN: {item['en']}"
                else:
                    line = f"RU: {item['ru']}\nEN: {item['en']}"
            else:
                if OVERLAY_SHOW_TIMESTAMP:
                    line = f"[{timestamp}] {item['en']}"
                else:
                    line = f"{item['en']}"
            self.lines.append(line)

        self.text_var.set("\n".join(self.lines))

    def show_note(self, note: str):
        note = _norm_space(note)
        if note:
            self.text_var.set(note)


class SkyTranslator:
    def __init__(self):
        print("Initializing Sky Game Chat Translator...")

        # Load translation model (runs once at startup)
        print("Loading translation model (this may take a moment)...")
        model_name = "Helsinki-NLP/opus-mt-ru-en"
        self.tokenizer = MarianTokenizer.from_pretrained(model_name)
        self.model = MarianMTModel.from_pretrained(model_name)
        self.model.eval()

        self.device = torch.device("cpu")
        if USE_GPU_IF_AVAILABLE and torch.cuda.is_available():
            self.device = torch.device("cuda")
        print(f"Device: {self.device}")

        self.model.to(self.device)
        if self.device.type == "cuda" and USE_FP16_IF_CUDA:
            try:
                self.model.half()
                print("✓ Using FP16 on CUDA")
            except Exception:
                print("⚠ FP16 not supported; using FP32")

        print("✓ Model loaded successfully")

        # Chat region coordinates (left side of screen)
        # You'll need to adjust these based on your screen resolution
        self.chat_region = (0, 0, 631, 934)  # (left, top, right, bottom)

        # Store previous messages to avoid duplicates (deterministic eviction).
        self._seen_set = set()
        self._seen_order = deque()

        # Translation cache (avoids re-running the model for repeated messages).
        self._tr_cache = {}
        self._tr_cache_order = deque()
        self._tr_cache_max = 256

        # Optional faster capturer (falls back to ImageGrab).
        self._sct = None
        if USE_MSS and mss is not None:
            try:
                self._sct = mss.mss()
                print("✓ Using mss for screen capture")
            except Exception:
                self._sct = None

        print("\n" + "=" * 60)
        print("Sky Game Chat Translator v2 - Ready!")
        print("=" * 60)
        print(f"Chat capture region: {self.chat_region}")
        print(f"Press {CAPTURE_HOTKEY.upper()} to capture and translate chat")
        print(f"Press {PAUSE_TOGGLE_HOTKEY.upper()} to pause/resume (keeps model loaded)")
        print(f"Press {OVERLAY_TOGGLE_HOTKEY.upper()} to hide/show overlay")
        print(f"Press {QUIT_HOTKEY.upper()} to quit")
        print("=" * 60 + "\n")

    def capture_chat_region(self):
        """Capture the chat panel area of the screen"""
        if self._sct is not None:
            l, t, r, b = self.chat_region
            w = max(1, int(r - l))
            h = max(1, int(b - t))
            monitor = {"left": int(l), "top": int(t), "width": w, "height": h}
            shot = self._sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        else:
            img = ImageGrab.grab(bbox=self.chat_region)

        if OCR_DOWNSCALE and abs(OCR_DOWNSCALE - 1.0) > 1e-3:
            nw = max(1, int(img.size[0] * OCR_DOWNSCALE))
            nh = max(1, int(img.size[1] * OCR_DOWNSCALE))
            img = img.resize((nw, nh), Image.BILINEAR)

        return img

    def preprocess_image(self, image):
        """
        Preprocess image for better OCR accuracy
        - Convert to grayscale
        - Increase contrast
        - Apply thresholding
        """
        # Convert to grayscale
        gray = ImageOps.grayscale(image)

        # Increase contrast
        enhancer = ImageEnhance.Contrast(gray)
        contrasted = enhancer.enhance(2.0)

        # Apply binary threshold (PIL-native is typically faster than numpy here)
        threshold = 128
        lut = [0] * (threshold + 1) + [255] * (256 - (threshold + 1))
        processed = contrasted.point(lut)
        return processed

    def _ocr_lines_data(self, image):
        """Extract OCR words + confidences and reconstruct lines."""
        custom_config = r"--oem 3 --psm 6 -l rus"
        try:
            data = pytesseract.image_to_data(image, config=custom_config, output_type=Output.DICT)
        except Exception as e:
            print(f"OCR Error: {e}")
            return []

        grouped = {}
        words = data.get("text", [])
        n = len(words)
        for i in range(n):
            word = _norm_space(words[i])
            if not word:
                continue

            conf_raw = (data.get("conf", ["-1"] * n)[i] or "-1").strip()
            try:
                conf = float(conf_raw)
            except Exception:
                conf = -1.0
            if conf < 0:
                continue

            key = (
                data.get("block_num", [0] * n)[i],
                data.get("par_num", [0] * n)[i],
                data.get("line_num", [0] * n)[i],
            )

            x = int(data.get("left", [0] * n)[i])
            y = int(data.get("top", [0] * n)[i])
            w = int(data.get("width", [0] * n)[i])
            h = int(data.get("height", [0] * n)[i])
            box = (x, y, x + w, y + h)

            ent = grouped.get(key)
            if ent is None:
                grouped[key] = {"words": [word], "confs": [conf], "bbox": list(box)}
            else:
                ent["words"].append(word)
                ent["confs"].append(conf)
                ent["bbox"][0] = min(ent["bbox"][0], box[0])
                ent["bbox"][1] = min(ent["bbox"][1], box[1])
                ent["bbox"][2] = max(ent["bbox"][2], box[2])
                ent["bbox"][3] = max(ent["bbox"][3], box[3])

        lines = []
        for _, ent in sorted(grouped.items(), key=lambda kv: kv[0]):
            text = _norm_space(" ".join(ent["words"]))
            if not text:
                continue
            avg_conf = sum(ent["confs"]) / max(1, len(ent["confs"]))
            lines.append({"text": text, "avg_conf": avg_conf, "bbox": tuple(ent["bbox"])})

        return lines

    def _filter_messages(self, ocr_lines):
        """
        Convert OCR line candidates into message dicts:
        - Filter low confidence, dot-only, and non-cyrillic noise (e.g. hidden '......' lines)
        - Strip trailing speaker name ("... - Sofia") but keep it as metadata.
        """
        messages = []
        for ln in ocr_lines:
            text = ln.get("text", "")
            avg_conf = float(ln.get("avg_conf", 0.0))
            if avg_conf < MIN_OCR_CONF:
                continue

            msg, speaker = _split_speaker(text)
            speaker_l = speaker.lower().strip()
            if speaker_l and speaker_l in IGNORE_SPEAKERS:
                continue

            if not msg or len(msg) < MIN_MSG_CHARS:
                continue
            if _looks_like_dots_or_junk(msg):
                continue

            cyr_count, cyr_ratio = _cyrillic_stats(msg)
            if cyr_count < MIN_CYRILLIC_LETTERS or cyr_ratio < 0.25:
                continue

            messages.append(
                {
                    "ru": msg,
                    "speaker": speaker,
                    "key": _normalize_key(msg),
                    "avg_conf": avg_conf,
                    "bbox": ln.get("bbox"),
                }
            )
        return messages

    def translate_text(self, text):
        """Translate Russian text to English using MarianMT"""
        if not text:
            return ""

        try:
            # Tokenize
            inputs = self.tokenizer(
                text,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=MAX_SOURCE_TOKENS,
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Generate translation
            with torch.inference_mode():
                translated = self.model.generate(
                    **inputs,
                    num_beams=int(TRANSLATE_NUM_BEAMS),
                    max_new_tokens=int(MAX_NEW_TOKENS),
                    do_sample=False,
                    early_stopping=True,
                )

            # Decode
            translation = self.tokenizer.decode(translated[0], skip_special_tokens=True)

            return translation
        except Exception as e:
            print(f"Translation Error: {e}")
            return f"[Translation failed: {text}]"

    def _cache_get(self, key: str):
        return self._tr_cache.get(key)

    def _cache_put(self, key: str, value: str):
        if key in self._tr_cache:
            self._tr_cache[key] = value
            return
        if len(self._tr_cache_order) >= self._tr_cache_max:
            old = self._tr_cache_order.popleft()
            self._tr_cache.pop(old, None)
        self._tr_cache[key] = value
        self._tr_cache_order.append(key)

    def translate_batch(self, messages):
        """
        Translate a list of message dicts in one batch.
        Returns list of {"ru":..., "en":...} preserving order.
        """
        if not messages:
            return []

        out = [None] * len(messages)
        to_translate = []
        to_translate_keys = []
        positions = []

        for idx, m in enumerate(messages):
            key = m["key"]
            cached = self._cache_get(key)
            if cached is not None:
                out[idx] = {"ru": m["ru"], "en": cached}
            else:
                to_translate.append(m["ru"])
                to_translate_keys.append(key)
                positions.append(idx)

        if to_translate:
            inputs = self.tokenizer(
                to_translate,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=MAX_SOURCE_TOKENS,
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.inference_mode():
                generated = self.model.generate(
                    **inputs,
                    num_beams=int(TRANSLATE_NUM_BEAMS),
                    max_new_tokens=int(MAX_NEW_TOKENS),
                    do_sample=False,
                    early_stopping=True,
                )

            decoded = self.tokenizer.batch_decode(generated, skip_special_tokens=True)

            for pos, key, ru, en in zip(positions, to_translate_keys, to_translate, decoded):
                self._cache_put(key, en)
                out[pos] = {"ru": ru, "en": en}

        # Should be fully filled now.
        return out

    def _mark_seen(self, msg: str):
        if msg in self._seen_set:
            return False

        # Evict oldest deterministically to keep memory bounded.
        if len(self._seen_order) >= SEEN_CACHE_MAX:
            oldest = self._seen_order.popleft()
            self._seen_set.discard(oldest)

        self._seen_set.add(msg)
        self._seen_order.append(msg)
        return True

    def _dedupe_and_translate(self, cleaned_lines):
        # Deprecated: v2 uses OCR(data) + filtering + batching for speed/quality.
        translations = []
        for russian_line in cleaned_lines:
            key = _normalize_key(russian_line)
            if not self._mark_seen(key):
                continue
            english = self.translate_text(russian_line)
            translations.append({"ru": russian_line, "en": english})
        return translations

    def process_chat_once(self, stage_cb=None):
        """
        Capture → preprocess → OCR(data) → filter/dedupe → batch translate.
        Returns (timestamp, translations, note, timings_ms).
        """
        timings = {}
        t0 = _now_ms()
        timestamp = datetime.now().strftime("%H:%M:%S")

        if stage_cb:
            stage_cb("Capturing...")
        screenshot = self.capture_chat_region()
        timings["capture_ms"] = _now_ms() - t0

        if stage_cb:
            stage_cb("Preprocessing...")
        t1 = _now_ms()
        processed = self.preprocess_image(screenshot)
        timings["preprocess_ms"] = _now_ms() - t1

        if stage_cb:
            stage_cb("OCR...")
        t2 = _now_ms()
        ocr_lines = self._ocr_lines_data(processed)
        timings["ocr_ms"] = _now_ms() - t2

        if stage_cb:
            stage_cb("Filtering...")
        t3 = _now_ms()
        filtered = self._filter_messages(ocr_lines)
        # Dedupe using normalized key (after stripping speaker/spacing noise).
        messages = [m for m in filtered if self._mark_seen(m["key"])]
        timings["postprocess_ms"] = _now_ms() - t3

        if not messages:
            if len(filtered) > 0:
                note = "No new Russian messages (already translated)"
            else:
                note = "No Russian text detected (filtered noise)"
            timings["total_ms"] = _now_ms() - t0
            return timestamp, [], note, timings

        if stage_cb:
            stage_cb(f"Translating ({len(messages)})...")
        t4 = _now_ms()
        translations = self.translate_batch(messages)
        timings["translate_ms"] = _now_ms() - t4
        timings["total_ms"] = _now_ms() - t0

        print(
            f"\n[{timestamp}] {len(messages)} msg(s). Timings(ms): "
            f"cap {timings['capture_ms']:.0f}, pre {timings['preprocess_ms']:.0f}, "
            f"ocr {timings['ocr_ms']:.0f}, post {timings['postprocess_ms']:.0f}, "
            f"tr {timings['translate_ms']:.0f}, total {timings['total_ms']:.0f}"
        )
        for item in translations:
            print(f"  RU: {item['ru']}")
            print(f"  EN: {item['en']}\n")

        return timestamp, translations, "", timings


def _safe_put_nowait(q: queue.Queue, item):
    try:
        q.put_nowait(item)
        return True
    except queue.Full:
        return False


def run_app():
    translator = SkyTranslator()

    stop_event = threading.Event()
    paused_event = threading.Event()  # set => paused

    task_queue: queue.Queue = queue.Queue(maxsize=1)  # drop spam; keep app responsive
    ui_queue: queue.Queue = queue.Queue()

    overlay = None
    if OVERLAY_ENABLED:
        if tk is None:
            print("⚠ Overlay disabled: tkinter is not available in this environment.")
        else:
            overlay = OverlayWindow(translator.chat_region)

    def worker():
        while not stop_event.is_set():
            try:
                task = task_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if stop_event.is_set():
                    break
                if paused_event.is_set():
                    continue
                if task != "capture":
                    continue

                def stage_cb(s: str):
                    ui_queue.put({"type": "stage", "text": s})

                ts, translations, note, timings = translator.process_chat_once(stage_cb=stage_cb)
                ui_queue.put(
                    {
                        "type": "result",
                        "timestamp": ts,
                        "translations": translations,
                        "note": note,
                        "timings": timings,
                    }
                )
            except Exception as e:
                ui_queue.put({"type": "log", "text": f"Error: {e}"})
                import traceback

                traceback.print_exc()
            finally:
                try:
                    task_queue.task_done()
                except Exception:
                    pass

    t = threading.Thread(target=worker, name="sky-translate-worker", daemon=True)
    t.start()

    def on_capture_hotkey():
        # If paused: resume + capture (no restart penalty, model stays in memory).
        if paused_event.is_set():
            paused_event.clear()
            ui_queue.put({"type": "status", "paused": False})
        ui_queue.put({"type": "stage", "text": "Queued capture..."})
        ok = _safe_put_nowait(task_queue, "capture")
        if not ok:
            ui_queue.put({"type": "stage", "text": "Busy (try again)..."})

    def on_pause_toggle_hotkey():
        new_paused = not paused_event.is_set()
        if new_paused:
            paused_event.set()
        else:
            paused_event.clear()
        ui_queue.put({"type": "status", "paused": new_paused})

    def on_overlay_toggle_hotkey():
        ui_queue.put({"type": "toggle_overlay"})

    def on_quit_hotkey():
        stop_event.set()
        ui_queue.put({"type": "quit"})
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    keyboard.add_hotkey(CAPTURE_HOTKEY, on_capture_hotkey)
    keyboard.add_hotkey(PAUSE_TOGGLE_HOTKEY, on_pause_toggle_hotkey)
    keyboard.add_hotkey(OVERLAY_TOGGLE_HOTKEY, on_overlay_toggle_hotkey)
    keyboard.add_hotkey(QUIT_HOTKEY, on_quit_hotkey)

    print("Waiting for hotkeys...")

    if overlay is None:
        # No UI available; keep process alive until quit hotkey.
        try:
            while not stop_event.is_set():
                time.sleep(0.2)
        finally:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        return

    def poll_ui_queue():
        destroyed = False
        try:
            while True:
                msg = ui_queue.get_nowait()
                mtype = msg.get("type")
                if mtype == "stage":
                    overlay.set_stage(msg.get("text", ""))
                elif mtype == "result":
                    translations = msg.get("translations") or []
                    note = msg.get("note") or ""
                    timings = msg.get("timings") or {}
                    if translations:
                        overlay.push_translations(msg.get("timestamp", ""), translations)
                        overlay.set_stage(f"Done ({float(timings.get('total_ms', 0.0)):.0f}ms)")
                    else:
                        # Don't wipe previous translations; just show a status note.
                        if len(overlay.lines) == 0:
                            overlay.show_note(note or "No output")
                        overlay.set_stage(note or "Done")
                elif mtype == "status":
                    overlay.set_paused(bool(msg.get("paused")))
                elif mtype == "toggle_overlay":
                    overlay.toggle_visibility()
                elif mtype == "log":
                    # minimal: show in console; avoid overlay spam
                    print(msg.get("text", ""))
                elif mtype == "quit":
                    destroyed = True
                    overlay.root.destroy()
                    break
        except queue.Empty:
            pass
        if not destroyed:
            overlay.root.after(75, poll_ui_queue)

    overlay.root.after(75, poll_ui_queue)
    overlay.root.mainloop()

    stop_event.set()
    try:
        keyboard.unhook_all_hotkeys()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        run_app()
    except KeyboardInterrupt:
        sys.exit(0)
