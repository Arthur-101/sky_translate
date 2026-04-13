"""
Helper script to find the correct chat region coordinates
Run this before using the main translator
"""

import pyautogui
import time

print("="*60)
print("Chat Region Coordinate Finder")
print("="*60)
print("\nThis script will help you find the coordinates for your")
print("Sky: Children of the Light chat panel.\n")

print("Instructions:")
print("1. Open Sky: Children of the Light")
print("2. Open the chat panel (left side)")
print("3. Follow the prompts below\n")

input("Press Enter when ready...")

print("\n" + "="*60)
print("STEP 1: Find TOP-LEFT corner of chat panel")
print("="*60)
print("Move your mouse to the TOP-LEFT corner of the chat panel")
print("(where the chat box begins)")

for i in range(5, 0, -1):
    print(f"Reading position in {i}...", end="\r")
    time.sleep(1)

tl = pyautogui.position()
print(f"\n✓ Top-Left corner: ({tl.x}, {tl.y})                    ")

print("\n" + "="*60)
print("STEP 2: Find BOTTOM-RIGHT corner of chat panel")
print("="*60)
print("Move your mouse to the BOTTOM-RIGHT corner of the chat panel")
print("(where the chat box ends)")

for i in range(5, 0, -1):
    print(f"Reading position in {i}...", end="\r")
    time.sleep(1)

br = pyautogui.position()
print(f"\n✓ Bottom-Right corner: ({br.x}, {br.y})                    ")

print("\n" + "="*60)
print("RESULTS")
print("="*60)
print(f"\nTop-Left:     ({tl.x}, {tl.y})")
print(f"Bottom-Right: ({br.x}, {br.y})")
print(f"\nWidth:  {br.x - tl.x} pixels")
print(f"Height: {br.y - tl.y} pixels")

chat_region = (tl.x, tl.y, br.x, br.y)
print(f"\n" + "="*60)
print("COPY THIS LINE INTO sky_translator.py")
print("="*60)
print(f"\nself.chat_region = {chat_region}")
print("\nReplace line 24 in sky_translator.py with the above line.")
print("\n" + "="*60)

input("\nPress Enter to exit...")