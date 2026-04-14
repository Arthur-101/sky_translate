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
from collections import deque
from datetime import datetime

from PIL import Image, ImageGrab, ImageEnhance, ImageOps
import pytesseract
from transformers import MarianMTModel, MarianTokenizer
import keyboard
import numpy as np

try:
    import tkinter as tk
except Exception:  # pragma: no cover (environment-specific)
    tk = None


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
OVERLAY_FONT = ("Segoe UI", 12)
OVERLAY_STATUS_FONT = ("Segoe UI", 11, "bold")

SEEN_CACHE_MAX = 120  # bounds memory + prevents infinite duplicate suppression


class OverlayWindow:
    def __init__(self, chat_region):
        if tk is None:
            raise RuntimeError("tkinter is not available in this Python environment.")

        self.chat_region = chat_region
        self.visible = True
        self.paused = False
        self.lines = deque(maxlen=OVERLAY_MAX_LINES)

        self.root = tk.Tk()
        self.root.title("Sky Translator Overlay")
        self.root.overrideredirect(True)  # borderless
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(OVERLAY_ALPHA))

        # Make it look intentional and readable on top of the game.
        self.root.configure(bg="#0b0f14")

        self.status_var = tk.StringVar(value="RUNNING  |  F9 capture  ESC pause  F10 hide  F8 quit")
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
            self.status_var.set("PAUSED  |  F9 resume+capture  ESC resume  F10 hide  F8 quit")
        else:
            self.status_var.set("RUNNING  |  F9 capture  ESC pause  F10 hide  F8 quit")

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
                line = f"[{timestamp}] RU: {item['ru']}\n[{timestamp}] EN: {item['en']}"
            else:
                line = f"[{timestamp}] {item['en']}"
            self.lines.append(line)

        self.text_var.set("\n\n".join(self.lines))


class SkyTranslator:
    def __init__(self):
        print("Initializing Sky Game Chat Translator...")
        
        # Load translation model (runs once at startup)
        print("Loading translation model (this may take a moment)...")
        model_name = "Helsinki-NLP/opus-mt-ru-en"
        self.tokenizer = MarianTokenizer.from_pretrained(model_name)
        self.model = MarianMTModel.from_pretrained(model_name)
        print("✓ Model loaded successfully")
        
        # Chat region coordinates (left side of screen)
        # You'll need to adjust these based on your screen resolution
        self.chat_region = (0, 0, 631, 934)  # (left, top, right, bottom)
        
        # Store previous messages to avoid duplicates (deterministic eviction).
        self._seen_set = set()
        self._seen_order = deque()
        
        print("\n" + "="*60)
        print("Sky Game Chat Translator v2 - Ready!")
        print("="*60)
        print(f"Chat capture region: {self.chat_region}")
        print(f"Press {CAPTURE_HOTKEY.upper()} to capture and translate chat")
        print(f"Press {PAUSE_TOGGLE_HOTKEY.upper()} to pause/resume (keeps model loaded)")
        print(f"Press {OVERLAY_TOGGLE_HOTKEY.upper()} to hide/show overlay")
        print(f"Press {QUIT_HOTKEY.upper()} to quit")
        print("="*60 + "\n")
    
    def capture_chat_region(self):
        """Capture the chat panel area of the screen"""
        screenshot = ImageGrab.grab(bbox=self.chat_region)
        return screenshot
    
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
        
        # Convert to numpy array for thresholding
        img_array = np.array(contrasted)
        
        # Apply binary threshold (adjust threshold value as needed)
        threshold = 128
        binary = np.where(img_array > threshold, 255, 0).astype(np.uint8)
        
        # Convert back to PIL Image
        processed = Image.fromarray(binary)
        
        return processed
    
    def extract_text_ocr(self, image):
        """Extract Russian text using Tesseract OCR"""
        # Configure Tesseract for Russian
        custom_config = r'--oem 3 --psm 6 -l rus'
        
        try:
            text = pytesseract.image_to_string(image, config=custom_config)
            return text
        except Exception as e:
            print(f"OCR Error: {e}")
            return ""
    
    def clean_text(self, text):
        """
        Clean OCR output
        - Keep only lines with Cyrillic characters
        - Remove artifacts
        - Preserve punctuation
        """
        if not text or text.strip() == "":
            return []
        
        lines = text.strip().split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            # Check if line contains Cyrillic characters
            has_cyrillic = any('\u0400' <= char <= '\u04FF' for char in line)
            
            if has_cyrillic:
                # Remove excessive whitespace
                line = ' '.join(line.split())
                cleaned_lines.append(line)
        
        return cleaned_lines
    
    def translate_text(self, text):
        """Translate Russian text to English using MarianMT"""
        if not text:
            return ""
        
        try:
            # Tokenize
            inputs = self.tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
            
            # Generate translation
            translated = self.model.generate(**inputs)
            
            # Decode
            translation = self.tokenizer.decode(translated[0], skip_special_tokens=True)
            
            return translation
        except Exception as e:
            print(f"Translation Error: {e}")
            return f"[Translation failed: {text}]"

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
        translations = []
        for russian_line in cleaned_lines:
            if not self._mark_seen(russian_line):
                continue
            english = self.translate_text(russian_line)
            translations.append({"ru": russian_line, "en": english})
        return translations

    def process_chat_once(self):
        """Capture → preprocess → OCR → clean → translate. Returns overlay-ready translations."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{timestamp}] Capturing chat...")

        screenshot = self.capture_chat_region()
        processed = self.preprocess_image(screenshot)

        print("Running OCR...")
        raw_text = self.extract_text_ocr(processed)
        cleaned_lines = self.clean_text(raw_text)
        if not cleaned_lines:
            print("⚠ No Russian text detected")
            return timestamp, []

        print(f"✓ Found {len(cleaned_lines)} line(s) of Russian text")
        translations = self._dedupe_and_translate(cleaned_lines)
        for item in translations:
            print(f"  RU: {item['ru']}")
            print(f"  EN: {item['en']}\n")
        return timestamp, translations


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

                ts, translations = translator.process_chat_once()
                if translations:
                    ui_queue.put({"type": "translations", "timestamp": ts, "translations": translations})
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
        _safe_put_nowait(task_queue, "capture")

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
                if mtype == "translations":
                    overlay.push_translations(msg["timestamp"], msg["translations"])
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
