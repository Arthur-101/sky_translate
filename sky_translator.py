"""
Sky Game Chat Translator v1 - Console Output
Captures chat region, extracts Russian text via OCR, translates to English
"""

import time
from PIL import Image, ImageGrab, ImageEnhance, ImageOps
import pytesseract
from transformers import MarianMTModel, MarianTokenizer
import keyboard
import numpy as np
from datetime import datetime

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
        
        # Store previous messages to avoid duplicates
        self.seen_messages = set()
        
        print("\n" + "="*60)
        print("Sky Game Chat Translator v1 - Ready!")
        print("="*60)
        print(f"Chat capture region: {self.chat_region}")
        print("Press F9 to capture and translate chat")
        print("Press ESC to exit")
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
    
    def process_chat(self):
        """Main processing pipeline: Capture → OCR → Clean → Translate"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{timestamp}] Capturing chat...")
        
        # Step 1: Capture screen region
        screenshot = self.capture_chat_region()
        
        # Step 2: Preprocess image
        processed = self.preprocess_image(screenshot)
        
        # Optional: Save processed image for debugging
        # processed.save(f"debug_capture_{int(time.time())}.png")
        
        # Step 3: OCR
        print("Running OCR...")
        raw_text = self.extract_text_ocr(processed)
        
        # Step 4: Clean text
        cleaned_lines = self.clean_text(raw_text)
        
        if not cleaned_lines:
            print("⚠ No Russian text detected")
            return
        
        print(f"✓ Found {len(cleaned_lines)} line(s) of Russian text\n")
        
        # Step 5: Translate each line
        for i, russian_line in enumerate(cleaned_lines, 1):
            # Skip if we've seen this exact message before
            if russian_line in self.seen_messages:
                continue
            
            self.seen_messages.add(russian_line)
            
            print(f"Message {i}:")
            print(f"  RU: {russian_line}")
            
            # Translate
            english = self.translate_text(russian_line)
            print(f"  EN: {english}")
            print()
        
        # Limit seen messages cache to prevent memory issues
        if len(self.seen_messages) > 100:
            # Keep only the 50 most recent
            self.seen_messages = set(list(self.seen_messages)[-50:])
    
    def run(self):
        """Main loop - wait for hotkey"""
        print("Waiting for input...")
        
        while True:
            # F9 to capture and translate
            if keyboard.is_pressed('f9'):
                try:
                    self.process_chat()
                except Exception as e:
                    print(f"Error: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Small delay to prevent multiple triggers
                time.sleep(0.5)
            
            # ESC to exit
            if keyboard.is_pressed('esc'):
                print("\nExiting...")
                break
            
            # Small sleep to reduce CPU usage
            time.sleep(0.1)

if __name__ == "__main__":
    translator = SkyTranslator()
    translator.run()
