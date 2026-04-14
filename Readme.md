# Sky Game Chat Translator - Setup Guide (v2 Overlay + Pause/Resume)

## Prerequisites

### 1. Install Tesseract OCR

**Windows:**
1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer (tesseract-ocr-w64-setup-5.x.x.exe)
3. **IMPORTANT**: During installation, make sure to select "Russian" language data
4. Note the installation path (default: `C:\Program Files\Tesseract-OCR`)
5. Add Tesseract to your PATH:
   - Right-click "This PC" → Properties → Advanced System Settings
   - Click "Environment Variables"
   - Under "System Variables", find "Path" and click "Edit"
   - Click "New" and add: `C:\Program Files\Tesseract-OCR`
   - Click OK on all dialogs

**Alternative**: If you don't add to PATH, you can set it in the script:
```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

### 2. Install Python Dependencies

Open Command Prompt or PowerShell and run:

```bash
pip install -r requirements.txt
```

This will install:
- Pillow (image processing)
- pytesseract (OCR wrapper)
- transformers (translation model)
- torch (neural network framework)
- keyboard (hotkey detection)
- numpy (numerical operations)

**Note**: Installing torch may take a few minutes as it's a large package.

## Configuration

### Adjust Chat Region Coordinates

Before running, you need to set the correct screen coordinates for your chat panel:

1. Open Sky: Children of the Light
2. Open the chat panel (left side of screen)
3. Note the position:
   - Where does the chat panel start? (left, top coordinates)
   - Where does it end? (right, bottom coordinates)

4. Edit `sky_translator.py` and find `self.chat_region = (...)` inside `SkyTranslator.__init__`:
   ```python
   self.chat_region = (50, 300, 450, 700)  # Adjust these numbers
   ```
   
   Format: `(left, top, right, bottom)` in pixels from top-left corner of screen

**Tips for finding coordinates:**
- Use Windows Snipping Tool to see pixel positions
- Or use this helper script:

```python
from PIL import ImageGrab
import pyautogui

# Move your mouse to top-left of chat panel
print("Move mouse to TOP-LEFT of chat panel, then press Enter")
input()
tl = pyautogui.position()
print(f"Top-Left: {tl}")

# Move your mouse to bottom-right of chat panel
print("Move mouse to BOTTOM-RIGHT of chat panel, then press Enter")
input()
br = pyautogui.position()
print(f"Bottom-Right: {br}")

print(f"\nYour chat_region should be: ({tl.x}, {tl.y}, {br.x}, {br.y})")
```

## Running the Translator

### Method 1: Direct Python

```bash
python sky_translator.py
```

### Method 2: Run as Administrator (Recommended for Windows)

Right-click Command Prompt → "Run as Administrator", then:

```bash
cd path\to\your\project
python sky_translator.py
```

**Why administrator?** The `keyboard` library needs elevated permissions to detect hotkeys globally.

## Usage

1. Start the translator script
2. Wait for "Sky Game Chat Translator v2 - Ready!" message
3. Open Sky: Children of the Light
4. Open the chat panel
5. Press **F9** to capture and translate (translations show in the overlay)
6. Press **ESC** to pause/resume (this keeps the model loaded, so it resumes fast)
7. Press **F10** to hide/show the overlay window
8. Press **F8** to quit the program

## First Run

The first time you run the script:
- It will download the translation model (~300MB)
- This happens automatically
- Model is cached locally for future runs
- Subsequent runs start much faster

## Troubleshooting

### "Tesseract not found"
- Make sure Tesseract is installed
- Check that it's in your PATH
- Or set the path manually in the script

### "No Russian text detected"
- Check your chat_region coordinates
- Make sure chat panel is open and visible
- Try adjusting preprocessing parameters (contrast, threshold)

### "Permission denied" for keyboard
- Run as administrator
- Or use a different hotkey library

### Overlay doesn't appear
- Make sure your Python installation includes `tkinter` (it usually does on Windows)
- If `tkinter` is missing, reinstall Python with the "tcl/tk" option enabled

### Model download is slow
- Be patient, it's a one-time download
- Make sure you have stable internet
- The model will be cached in: `~/.cache/huggingface/`

### OCR accuracy is poor
- Increase the chat_region size
- Adjust preprocessing parameters in the code
- Make sure your game UI scaling is at 100%
- Consider increasing in-game text size

## Next Steps

Once v2 is working:
- **v3**: Auto-detect chat panel open, align translations with chat lines
- **Future**: Support other languages (Chinese, etc.)

## File Structure

```
sky_translate/
│
├── sky_translator.py      # Main application
├── requirements.txt       # Python dependencies
├── find_coordinates.py    # Helper to find chat region
└── Readme.md              # This file
```

## Performance Notes

**CPU Usage:**
- Idle: ~1-5%
- During capture/translate: ~30-50% for 1-2 seconds
- Translation model runs on CPU by default

**GPU Acceleration (Optional):**
To use your RTX 2050 for faster translation:
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

Then the model will automatically use CUDA if available.

## Tips

1. **Optimal game settings:**
   - Increase chat text size in-game
   - Use solid background if possible
   - 100% UI scaling

2. **Better OCR:**
   - Capture region should be just the chat area
   - Avoid including decorative UI elements
   - Make sure text is clearly visible

3. **Testing:**
   - Save debug images to see what OCR is seeing
   - Uncomment the debug save line in `sky_translator.py` (search for `debug_capture_`):
     ```python
     processed.save(f"debug_capture_{int(time.time())}.png")
     ```

## Credits

- OCR: Tesseract (Apache License 2.0)
- Translation: Helsinki-NLP MarianMT (Apache License 2.0)
- Python libraries: See requirements.txt
