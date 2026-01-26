#!/usr/bin/env python3
"""
Robot Rave Controller - Backend with LED EYES!
(Pi + USB mic + 2 DC motors + Freenove 8x16 LED Matrix)

FEATURES:
- Music-reactive dancing with beat detection
- LED matrix eyes that react to music and movement
- Personality animations (intro, dance, idle, sleep)
- Synchronized eye movements with robot direction

HARDWARE:
- Raspberry Pi 4 with CamJam EduKit3
- Freenove 8x16 LED Matrix (HT16K33) via I2C
- USB Microphone

WIRING (LED Matrix to CamJam breakout):
  VCC -> 3v3
  GND -> GND
  SDA -> SDA
  SCL -> SCL

INSTALL:
  sudo raspi-config  # Enable I2C under Interface Options
  pip3 install flask numpy sounddevice scipy --break-system-packages
  pip3 install adafruit-circuitpython-ht16k33 --break-system-packages
  sudo apt-get install -y portaudio19-dev i2c-tools

RUN:
  sudo python3 robot_backend_with_eyes.py
"""

import os
import time
import threading
import collections
import logging
import queue
import random

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from enum import Enum

import numpy as np
from scipy import signal
from scipy.ndimage import maximum_filter1d
import sounddevice as sd
from flask import Flask, jsonify
import RPi.GPIO as GPIO

# ============================================================
# LED MATRIX SETUP (Graceful fallback if not available)
# ============================================================
LED_AVAILABLE = False
led_matrices = []

try:
    import board
    import busio
    from adafruit_ht16k33.matrix import Matrix8x8
    
    # Try to initialize I2C and LED matrices
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        # Freenove 8x16 has two matrices at addresses 0x70 and 0x71
        matrix_left = Matrix8x8(i2c, address=0x70)   # Left eye
        matrix_right = Matrix8x8(i2c, address=0x71)  # Right eye
        led_matrices = [matrix_left, matrix_right]
        LED_AVAILABLE = True
        print("[EYES] LED matrices initialized successfully!")
    except Exception as e:
        print(f"[EYES] Could not initialize LED matrices: {e}")
        print("[EYES] Running without LED eyes (simulation mode)")
except ImportError:
    print("[EYES] LED libraries not installed. Running without LED eyes.")
    print("[EYES] Install with: pip3 install adafruit-circuitpython-ht16k33 --break-system-packages")


# ============================================================
# 1. HARDWARE CONFIGURATION
# ============================================================
PIN_LEFT_1 = 9
PIN_LEFT_2 = 10
PIN_RIGHT_1 = 7
PIN_RIGHT_2 = 8
LED_PIN = 25

PWM_HZ = 100

# ============================================================
# 2. AUDIO CONFIGURATION
# ============================================================
SAMPLE_RATE = 44100
CHUNK_SIZE = 2048
HOP_SIZE = 512

FREQ_BANDS = {
    'sub_bass': (20, 60),
    'bass': (60, 250),
    'low_mid': (250, 500),
    'mid': (500, 2000),
    'high_mid': (2000, 4000),
    'treble': (4000, 8000),
    'air': (8000, 16000),
}

# ============================================================
# 3. LED EYE PATTERNS (8x8 bitmaps for each eye)
# ============================================================
# Each pattern is a list of 8 bytes, one per row
# 1 = LED on, 0 = LED off

EYE_PATTERNS = {
    # === BASIC EXPRESSIONS ===
    "normal": [
        0b00111100,
        0b01111110,
        0b11111111,
        0b11100111,  # Pupil
        0b11100111,  # Pupil
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    "normal_right": [  # Mirror for right eye (pupil on other side)
        0b00111100,
        0b01111110,
        0b11111111,
        0b11100111,
        0b11100111,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    # Happy - curved like ^_^
    "happy": [
        0b00000000,
        0b00000000,
        0b01111110,
        0b11111111,
        0b11111111,
        0b01111110,
        0b00111100,
        0b00000000,
    ],
    
    # Excited - wide open
    "excited": [
        0b00111100,
        0b01111110,
        0b11111111,
        0b11000011,  # Big pupil
        0b11000011,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    # Wide - even bigger (for beat reaction)
    "wide": [
        0b01111110,
        0b11111111,
        0b11111111,
        0b11100111,
        0b11100111,
        0b11111111,
        0b11111111,
        0b01111110,
    ],
    
    # Small - contracted (for beat pulse)
    "small": [
        0b00000000,
        0b00011000,
        0b00111100,
        0b01100110,
        0b01100110,
        0b00111100,
        0b00011000,
        0b00000000,
    ],
    
    # Medium - between small and normal
    "medium": [
        0b00000000,
        0b00111100,
        0b01111110,
        0b01100110,
        0b01100110,
        0b01111110,
        0b00111100,
        0b00000000,
    ],
    
    # === LOOKING DIRECTIONS ===
    "look_left": [
        0b00111100,
        0b01111110,
        0b11111111,
        0b11110011,  # Pupil left
        0b11110011,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    "look_right": [
        0b00111100,
        0b01111110,
        0b11111111,
        0b11001111,  # Pupil right
        0b11001111,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    "look_up": [
        0b00111100,
        0b01100110,  # Pupil up
        0b11100111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    "look_down": [
        0b00111100,
        0b01111110,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11100111,  # Pupil down
        0b01100110,
        0b00111100,
    ],
    
    # === EMOTIONS ===
    "angry_left": [
        0b11000000,
        0b01110000,
        0b00111100,
        0b00111110,
        0b00111110,
        0b00111100,
        0b01111110,
        0b00111100,
    ],
    
    "angry_right": [
        0b00000011,
        0b00001110,
        0b00111100,
        0b01111100,
        0b01111100,
        0b00111100,
        0b01111110,
        0b00111100,
    ],
    
    "sleepy": [
        0b00000000,
        0b00000000,
        0b00000000,
        0b01111110,
        0b11111111,
        0b11111111,
        0b01111110,
        0b00111100,
    ],
    
    "closed": [
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b01111110,
        0b11111111,
        0b01111110,
        0b00000000,
    ],
    
    "wink": [
        0b00000000,
        0b00000000,
        0b00000000,
        0b00111100,
        0b01111110,
        0b00111100,
        0b00000000,
        0b00000000,
    ],
    
    # === SPECIAL ===
    "heart": [
        0b01100110,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b01111110,
        0b00111100,
        0b00011000,
    ],
    
    "star": [
        0b00011000,
        0b00011000,
        0b11111111,
        0b01111110,
        0b00111100,
        0b01100110,
        0b11000011,
        0b00000000,
    ],
    
    "dizzy": [
        0b00111100,
        0b01000010,
        0b10011001,
        0b10100101,
        0b10100101,
        0b10011001,
        0b01000010,
        0b00111100,
    ],
    
    "dead": [
        0b11000011,
        0b01100110,
        0b00111100,
        0b00011000,
        0b00011000,
        0b00111100,
        0b01100110,
        0b11000011,
    ],
    
    # === BOOT SEQUENCE ===
    "scan_1": [
        0b11111111,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    "scan_2": [
        0b11111111,
        0b11111111,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    "scan_3": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    "scan_4": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    "scan_5": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    "scan_6": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b00000000,
        0b00000000,
    ],
    
    "scan_7": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b00000000,
    ],
    
    "scan_8": [
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
        0b11111111,
    ],
    
    # Off
    "off": [
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
    
    # Dot (for boot)
    "dot": [
        0b00000000,
        0b00000000,
        0b00000000,
        0b00011000,
        0b00011000,
        0b00000000,
        0b00000000,
        0b00000000,
    ],
}


# ============================================================
# 4. ROBOT EYES CLASS
# ============================================================

class EyeExpression(Enum):
    """Eye expression states"""
    OFF = "off"
    NORMAL = "normal"
    HAPPY = "happy"
    EXCITED = "excited"
    WIDE = "wide"
    SMALL = "small"
    SLEEPY = "sleepy"
    CLOSED = "closed"
    ANGRY = "angry"
    HEART = "heart"
    STAR = "star"
    DIZZY = "dizzy"
    DEAD = "dead"
    LOOK_LEFT = "look_left"
    LOOK_RIGHT = "look_right"
    LOOK_UP = "look_up"
    LOOK_DOWN = "look_down"


class RobotEyes:
    """
    Controls the LED matrix eyes with music-reactive animations.
    
    Features:
    - Boot sequence animation
    - Beat-synced reactions (pulse, wide, flash)
    - Movement-synced eye direction
    - Idle behaviors (blink, look around)
    - Emotion expressions
    - Sleep sequence
    """
    
    def __init__(self):
        self.enabled = LED_AVAILABLE
        self.matrices = led_matrices if LED_AVAILABLE else []
        
        # Current state
        self.current_expression = "normal"
        self.current_left = EYE_PATTERNS["normal"]
        self.current_right = EYE_PATTERNS["normal"]
        
        # Animation state
        self.animation_active = False
        self.animation_thread = None
        self.stop_animation = threading.Event()
        
        # Timing
        self.last_blink = time.time()
        self.next_blink_interval = random.uniform(2.0, 5.0)
        self.last_look_around = time.time()
        self.next_look_interval = random.uniform(5.0, 10.0)
        
        # Beat reaction state
        self.beat_reaction_until = 0
        self.is_reacting_to_beat = False
        
        # Movement tracking
        self.current_movement = "stop"
        
        # Boot flag
        self.has_booted = False
        
        # Brightness (0.0 to 1.0)
        self.brightness = 1.0
        
        # Expression lock (prevent changes during animations)
        self.expression_lock = threading.Lock()
        
        # Initialize displays
        if self.enabled:
            self._set_brightness(1.0)
            self._clear()
    
    def _set_brightness(self, brightness: float):
        """Set LED brightness (0.0 to 1.0)"""
        self.brightness = max(0.0, min(1.0, brightness))
        if self.enabled:
            for matrix in self.matrices:
                # HT16K33 brightness is 0-15
                matrix.brightness = self.brightness
    
    def _clear(self):
        """Turn off all LEDs"""
        if self.enabled:
            for matrix in self.matrices:
                matrix.fill(0)
                matrix.show()
    
    def _display_pattern(self, left_pattern: List[int], right_pattern: List[int]):
        """Display patterns on both eye matrices"""
        if not self.enabled:
            return
        
        try:
            # Left eye (matrix 0)
            if len(self.matrices) > 0:
                for row in range(8):
                    for col in range(8):
                        pixel_on = (left_pattern[row] >> (7 - col)) & 1
                        self.matrices[0][col, row] = pixel_on
                self.matrices[0].show()
            
            # Right eye (matrix 1)
            if len(self.matrices) > 1:
                for row in range(8):
                    for col in range(8):
                        pixel_on = (right_pattern[row] >> (7 - col)) & 1
                        self.matrices[1][col, row] = pixel_on
                self.matrices[1].show()
        except Exception as e:
            print(f"[EYES] Display error: {e}")
    
    def set_expression(self, expression: str, left_pattern: str = None, right_pattern: str = None):
        """
        Set eye expression.
        
        Args:
            expression: Name of expression (e.g., "normal", "happy")
            left_pattern: Override pattern for left eye
            right_pattern: Override pattern for right eye
        """
        with self.expression_lock:
            self.current_expression = expression
            
            # Get patterns
            if left_pattern and left_pattern in EYE_PATTERNS:
                self.current_left = EYE_PATTERNS[left_pattern]
            elif expression in EYE_PATTERNS:
                self.current_left = EYE_PATTERNS[expression]
            
            if right_pattern and right_pattern in EYE_PATTERNS:
                self.current_right = EYE_PATTERNS[right_pattern]
            elif expression in EYE_PATTERNS:
                self.current_right = EYE_PATTERNS[expression]
            
            # Special handling for asymmetric expressions
            if expression == "angry":
                self.current_left = EYE_PATTERNS["angry_left"]
                self.current_right = EYE_PATTERNS["angry_right"]
            elif expression == "wink_left":
                self.current_left = EYE_PATTERNS["wink"]
                self.current_right = EYE_PATTERNS["normal"]
            elif expression == "wink_right":
                self.current_left = EYE_PATTERNS["normal"]
                self.current_right = EYE_PATTERNS["wink"]
            
            self._display_pattern(self.current_left, self.current_right)
    
    def boot_sequence(self):
        """Play the startup animation"""
        if not self.enabled:
            self.has_booted = True
            return
        
        print("[EYES] Playing boot sequence...")
        
        # Phase 1: Scan on (CRT style)
        scan_patterns = ["scan_1", "scan_2", "scan_3", "scan_4", 
                        "scan_5", "scan_6", "scan_7", "scan_8"]
        for pattern in scan_patterns:
            self._display_pattern(EYE_PATTERNS[pattern], EYE_PATTERNS[pattern])
            time.sleep(0.08)
        
        time.sleep(0.2)
        
        # Phase 2: Flash and clear
        self._display_pattern(EYE_PATTERNS["off"], EYE_PATTERNS["off"])
        time.sleep(0.1)
        self._display_pattern(EYE_PATTERNS["scan_8"], EYE_PATTERNS["scan_8"])
        time.sleep(0.1)
        self._display_pattern(EYE_PATTERNS["off"], EYE_PATTERNS["off"])
        time.sleep(0.2)
        
        # Phase 3: Eyes form from dot
        self._display_pattern(EYE_PATTERNS["dot"], EYE_PATTERNS["dot"])
        time.sleep(0.15)
        self._display_pattern(EYE_PATTERNS["small"], EYE_PATTERNS["small"])
        time.sleep(0.15)
        self._display_pattern(EYE_PATTERNS["medium"], EYE_PATTERNS["medium"])
        time.sleep(0.15)
        self._display_pattern(EYE_PATTERNS["normal"], EYE_PATTERNS["normal"])
        time.sleep(0.3)
        
        # Phase 4: Look around (system check)
        self._display_pattern(EYE_PATTERNS["look_left"], EYE_PATTERNS["look_left"])
        time.sleep(0.2)
        self._display_pattern(EYE_PATTERNS["look_right"], EYE_PATTERNS["look_right"])
        time.sleep(0.2)
        self._display_pattern(EYE_PATTERNS["look_up"], EYE_PATTERNS["look_up"])
        time.sleep(0.15)
        self._display_pattern(EYE_PATTERNS["look_down"], EYE_PATTERNS["look_down"])
        time.sleep(0.15)
        self._display_pattern(EYE_PATTERNS["normal"], EYE_PATTERNS["normal"])
        time.sleep(0.2)
        
        # Phase 5: Ready wink
        self._display_pattern(EYE_PATTERNS["wink"], EYE_PATTERNS["normal"])
        time.sleep(0.25)
        self._display_pattern(EYE_PATTERNS["happy"], EYE_PATTERNS["happy"])
        time.sleep(0.3)
        self._display_pattern(EYE_PATTERNS["normal"], EYE_PATTERNS["normal"])
        
        self.has_booted = True
        self.current_expression = "normal"
        print("[EYES] Boot sequence complete!")
    
    def sleep_sequence(self):
        """Play the sleep/shutdown animation"""
        if not self.enabled:
            return
        
        # Gradually get sleepy
        self.set_expression("normal")
        time.sleep(0.3)
        self.set_expression("sleepy")
        time.sleep(0.5)
        
        # Blink slowly
        self.set_expression("closed")
        time.sleep(0.3)
        self.set_expression("sleepy")
        time.sleep(0.4)
        self.set_expression("closed")
        time.sleep(0.2)
        
        # Final close
        self._display_pattern(EYE_PATTERNS["off"], EYE_PATTERNS["off"])
    
    def on_beat(self, beat_strength: float, energy: float, bpm: int):
        """
        React to a detected beat.
        
        Args:
            beat_strength: 0-100 strength of beat
            energy: 0-100 current energy level
            bpm: Current BPM
        """
        if not self.enabled:
            return
        
        # Calculate reaction duration based on BPM
        if bpm > 0:
            beat_duration = 60.0 / bpm
            reaction_duration = beat_duration * 0.3  # React for 30% of beat
        else:
            reaction_duration = 0.1
        
        self.beat_reaction_until = time.time() + reaction_duration
        self.is_reacting_to_beat = True
        
        # Choose reaction based on energy and strength
        if beat_strength > 80 or energy > 80:
            # Strong beat - go wide!
            self.set_expression("wide")
        elif beat_strength > 50 or energy > 50:
            # Medium beat - excited
            self.set_expression("excited")
        else:
            # Light beat - quick pulse (small then back)
            self.set_expression("medium")
    
    def on_movement(self, direction: str):
        """
        Update eyes based on robot movement direction.
        
        Args:
            direction: forward, backward, left, right, spin, stop
        """
        self.current_movement = direction
        
        # Don't change if currently reacting to beat
        if self.is_reacting_to_beat and time.time() < self.beat_reaction_until:
            return
        
        if direction == "forward":
            self.set_expression("look_up")
        elif direction == "backward":
            self.set_expression("look_down")
        elif direction == "left":
            self.set_expression("look_left")
        elif direction == "right":
            self.set_expression("look_right")
        elif direction == "spin":
            # Dizzy eyes for spinning!
            self.set_expression("excited")
        elif direction == "stop":
            self.set_expression("normal")
    
    def on_spin_complete(self):
        """Show dizzy eyes after a spin"""
        if not self.enabled:
            return
        
        self.set_expression("dizzy")
        time.sleep(0.5)
        self.set_expression("normal")
    
    def trigger_special(self, special_type: str):
        """
        Trigger a special animation.
        
        Args:
            special_type: "heart", "star", "wink", "angry", "happy"
        """
        if not self.enabled:
            return
        
        if special_type == "heart":
            self.set_expression("heart")
        elif special_type == "star":
            self.set_expression("star")
        elif special_type == "wink":
            # Random wink side
            if random.random() > 0.5:
                self.set_expression("wink_left")
            else:
                self.set_expression("wink_right")
        elif special_type == "angry":
            self.set_expression("angry")
        elif special_type == "happy":
            self.set_expression("happy")
        elif special_type == "dead":
            self.set_expression("dead")
    
    def update_idle(self, energy: float = 0, is_music: bool = False):
        """
        Update idle behaviors (blinking, looking around).
        Call this regularly when not actively dancing.
        
        Args:
            energy: Current audio energy (0-100)
            is_music: Whether music is detected
        """
        if not self.enabled:
            return
        
        current_time = time.time()
        
        # Check if beat reaction has ended
        if self.is_reacting_to_beat and current_time >= self.beat_reaction_until:
            self.is_reacting_to_beat = False
            # Return to appropriate expression
            if is_music and energy > 30:
                self.set_expression("happy")
            else:
                self.set_expression("normal")
        
        # Skip idle animations if reacting to beat
        if self.is_reacting_to_beat:
            return
        
        # Natural blinking
        if current_time - self.last_blink >= self.next_blink_interval:
            self._do_blink()
            self.last_blink = current_time
            self.next_blink_interval = random.uniform(2.0, 5.0)
        
        # Look around occasionally (only when no music)
        if not is_music and current_time - self.last_look_around >= self.next_look_interval:
            self._do_look_around()
            self.last_look_around = current_time
            self.next_look_interval = random.uniform(5.0, 10.0)
    
    def _do_blink(self):
        """Perform a natural blink"""
        if not self.enabled:
            return
        
        # Save current expression
        saved_left = self.current_left
        saved_right = self.current_right
        
        # Quick blink
        self._display_pattern(EYE_PATTERNS["closed"], EYE_PATTERNS["closed"])
        time.sleep(0.08)
        
        # Restore
        self._display_pattern(saved_left, saved_right)
    
    def _do_look_around(self):
        """Look in a random direction briefly"""
        if not self.enabled:
            return
        
        directions = ["look_left", "look_right", "look_up"]
        direction = random.choice(directions)
        
        self.set_expression(direction)
        time.sleep(random.uniform(0.3, 0.6))
        self.set_expression("normal")
    
    def set_energy_expression(self, energy: float, is_dancing: bool):
        """
        Set expression based on energy level.
        
        Args:
            energy: 0-100 energy level
            is_dancing: Whether robot is currently dancing
        """
        if not self.enabled:
            return
        
        # Don't override beat reactions
        if self.is_reacting_to_beat and time.time() < self.beat_reaction_until:
            return
        
        if not is_dancing:
            if energy < 10:
                self.set_expression("sleepy")
            elif energy < 30:
                self.set_expression("normal")
            else:
                self.set_expression("happy")
        else:
            if energy > 80:
                self.set_expression("excited")
            elif energy > 50:
                self.set_expression("happy")
            else:
                self.set_expression("normal")
    
    def get_state(self) -> Dict:
        """Get current eye state for UI"""
        return {
            "enabled": self.enabled,
            "expression": self.current_expression,
            "is_reacting": self.is_reacting_to_beat,
            "has_booted": self.has_booted,
        }


# Create global eyes instance
robot_eyes = RobotEyes()


# ============================================================
# 5. GLOBAL STATE (THREAD-SAFE)
# ============================================================
ui_state = {
    # Basic status
    "status": "Booting...",
    "pattern_name": "None",
    "step_info": "Ready",
    "audio_ok": True,
    
    # Beat detection
    "bpm": 0,
    "bpm_confidence": 0,
    "is_beat": False,
    "beat_strength": 0,
    
    # Energy levels
    "energy": 0,
    "energy_bass": 0,
    "energy_mid": 0,
    "energy_treble": 0,
    
    # Music detection
    "music_gate": False,
    "music_confidence": 0,
    "dominant_band": "none",
    "style": "none",
    
    # Audio classification
    "audio_type": "SILENCE",
    "audio_type_confidence": 0,
    
    # Audio diagnostics
    "noise_floor": 0,
    "spectral_flux": 0,
    "spectral_flatness": 0,
    "spectral_centroid": 0,
    "onset_strength": 0,
    "zero_crossing_rate": 0,
    "rhythm_regularity": 0,
    
    # Waveform and spectrum for visualization
    "waveform": [0] * 64,
    "spectrum": [0] * 32,
    
    # User controls
    "sensitivity": 50,
    "gain": 50,
    "noise_gate_threshold": 0,
    "is_muted": False,
    "is_gated": False,
    "raw_rms": 0,
    
    # Dance state tracking
    "is_dancing": False,
    "current_move": "none",
    "move_intensity": 0,
    "patterns_completed": 0,
    "dance_reason": "",
    "beat_triggered": False,
    
    # NEW: Eye state
    "eye_expression": "normal",
    "eye_enabled": LED_AVAILABLE,
    "eye_reacting": False,
    
    # Debug info
    "debug_beat": False,
    "debug_timer": False,
    "debug_interval": 0,
    "debug_since_move": 0,
}

state_lock = threading.Lock()
autonomous_mode = False
stop_event = threading.Event()

motor_q = queue.Queue(maxsize=10)

# ============================================================
# 6. GPIO / MOTOR CONTROL
# ============================================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup([PIN_LEFT_1, PIN_LEFT_2, PIN_RIGHT_1, PIN_RIGHT_2, LED_PIN], GPIO.OUT)

pwm_l1 = GPIO.PWM(PIN_LEFT_1, PWM_HZ)
pwm_l2 = GPIO.PWM(PIN_LEFT_2, PWM_HZ)
pwm_r1 = GPIO.PWM(PIN_RIGHT_1, PWM_HZ)
pwm_r2 = GPIO.PWM(PIN_RIGHT_2, PWM_HZ)

pwm_l1.start(0)
pwm_l2.start(0)
pwm_r1.start(0)
pwm_r2.start(0)


def _clamp_dc(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def set_raw_motors(l_speed: float, r_speed: float):
    l = float(l_speed)
    r = float(r_speed)
    if l > 0:
        pwm_l1.ChangeDutyCycle(_clamp_dc(abs(l)))
        pwm_l2.ChangeDutyCycle(0)
    elif l < 0:
        pwm_l1.ChangeDutyCycle(0)
        pwm_l2.ChangeDutyCycle(_clamp_dc(abs(l)))
    else:
        pwm_l1.ChangeDutyCycle(0)
        pwm_l2.ChangeDutyCycle(0)
    if r > 0:
        pwm_r1.ChangeDutyCycle(_clamp_dc(abs(r)))
        pwm_r2.ChangeDutyCycle(0)
    elif r < 0:
        pwm_r1.ChangeDutyCycle(0)
        pwm_r2.ChangeDutyCycle(_clamp_dc(abs(r)))
    else:
        pwm_r1.ChangeDutyCycle(0)
        pwm_r2.ChangeDutyCycle(0)


def drive(direction: str, left_dc: float = 100, right_dc: float = 100):
    l = _clamp_dc(left_dc)
    r = _clamp_dc(right_dc)
    if direction == "stop":
        set_raw_motors(0, 0)
    elif direction == "forward":
        set_raw_motors(+l, -r)
    elif direction == "backward":
        set_raw_motors(-l, +r)
    elif direction == "left":
        set_raw_motors(+l, +r)
    elif direction == "right":
        set_raw_motors(-l, -r)
    elif direction == "spin":
        set_raw_motors(+l, +r)
    else:
        set_raw_motors(0, 0)


def stop_robot():
    drive("stop")
    GPIO.output(LED_PIN, GPIO.LOW)
    robot_eyes.on_movement("stop")
    with state_lock:
        ui_state["status"] = "Stopped"


def flash_led(duration=0.06):
    GPIO.output(LED_PIN, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(LED_PIN, GPIO.LOW)


def clear_motor_queue():
    while True:
        try:
            motor_q.get_nowait()
            motor_q.task_done()
        except queue.Empty:
            break


# ============================================================
# 7. AUDIO FEATURE EXTRACTION
# ============================================================

@dataclass
class AudioFeatures:
    """Container for all extracted audio features"""
    rms_energy: float = 0.0
    peak_amplitude: float = 0.0
    zero_crossing_rate: float = 0.0
    spectral_centroid: float = 0.0
    spectral_flatness: float = 0.0
    spectral_rolloff: float = 0.0
    spectral_flux: float = 0.0
    energy_sub_bass: float = 0.0
    energy_bass: float = 0.0
    energy_low_mid: float = 0.0
    energy_mid: float = 0.0
    energy_high_mid: float = 0.0
    energy_treble: float = 0.0
    dominant_band: str = "none"
    is_tonal: bool = False


class AudioAnalyzer:
    """Improved audio analysis with noise floor calibration"""
    
    def __init__(self, sample_rate: int, chunk_size: int):
        self.sr = sample_rate
        self.chunk_size = chunk_size
        self.window = np.hanning(chunk_size).astype(np.float32)
        self.freqs = np.fft.rfftfreq(chunk_size, 1.0 / sample_rate).astype(np.float32)
        self.noise_floor = 0.01
        self.noise_floor_alpha = 0.01
        self.prev_mag = None
        self.band_masks = {}
        for name, (f_lo, f_hi) in FREQ_BANDS.items():
            self.band_masks[name] = (self.freqs >= f_lo) & (self.freqs < f_hi)
    
    def calibrate_noise_floor(self, x: np.ndarray):
        energy = float(np.sqrt(np.mean(x ** 2)))
        if energy < self.noise_floor * 2:
            self.noise_floor = self.noise_floor * 0.95 + energy * 0.05
    
    def analyze(self, x: np.ndarray, gain: float = 1.0) -> AudioFeatures:
        features = AudioFeatures()
        
        if x.size < 128:
            return features
        
        x = x * gain
        
        features.rms_energy = float(np.sqrt(np.mean(x ** 2)))
        features.peak_amplitude = float(np.max(np.abs(x)))
        
        zero_crossings = np.sum(np.abs(np.diff(np.sign(x)))) / 2
        features.zero_crossing_rate = float(zero_crossings / len(x))
        
        windowed = (x * self.window).astype(np.float32)
        fft_result = np.fft.rfft(windowed)
        mag = np.abs(fft_result).astype(np.float32)
        mag = np.maximum(mag, 1e-10)
        
        mag_sum = float(np.sum(mag))
        if mag_sum > 0:
            features.spectral_centroid = float(np.sum(self.freqs * mag) / mag_sum)
        
        log_mag = np.log(mag + 1e-10)
        geo_mean = float(np.exp(np.mean(log_mag)))
        arith_mean = float(np.mean(mag))
        features.spectral_flatness = geo_mean / (arith_mean + 1e-10)
        
        cumsum = np.cumsum(mag)
        rolloff_idx = np.searchsorted(cumsum, 0.85 * cumsum[-1])
        if rolloff_idx < len(self.freqs):
            features.spectral_rolloff = float(self.freqs[rolloff_idx])
        
        if self.prev_mag is not None:
            diff = mag - self.prev_mag
            diff = np.maximum(diff, 0)
            features.spectral_flux = float(np.sum(diff))
        self.prev_mag = mag.copy()
        
        total_energy = float(np.sum(mag ** 2))
        if total_energy > 0:
            for name, mask in self.band_masks.items():
                band_energy = float(np.sum(mag[mask] ** 2))
                setattr(features, f'energy_{name}', band_energy / total_energy)
        
        bass_total = features.energy_sub_bass + features.energy_bass
        mid_total = features.energy_low_mid + features.energy_mid + features.energy_high_mid
        treble_total = features.energy_treble
        
        max_energy = max(bass_total, mid_total, treble_total)
        if max_energy < 0.2:
            features.dominant_band = "none"
        elif max_energy == bass_total:
            features.dominant_band = "bass"
        elif max_energy == treble_total:
            features.dominant_band = "treble"
        else:
            features.dominant_band = "mid"
        
        features.is_tonal = features.spectral_flatness < 0.3
        
        return features


# ============================================================
# 8. BEAT DETECTION
# ============================================================

class BeatDetector:
    """Improved beat detection with onset detection and autocorrelation BPM"""
    
    def __init__(self, sample_rate: int, hop_size: int):
        self.sr = sample_rate
        self.hop_size = hop_size
        self.onset_history = collections.deque(maxlen=50)
        self.onset_threshold_factor = 0.8
        self.energy_history = collections.deque(maxlen=30)
        self.energy_threshold_factor = 0.9
        self.beat_times = collections.deque(maxlen=32)
        self.bpm_history = collections.deque(maxlen=10)
        self.current_bpm = 120
        self.bpm_confidence = 0
        self.min_bpm = 60
        self.max_bpm = 180
        self.min_beat_interval = 60.0 / self.max_bpm * 0.6
        self.last_beat_time = 0
        self.onset_envelope = collections.deque(maxlen=256)
        self.high_energy_frames = 0
        self.last_fallback_beat = 0
    
    def detect_beat(self, spectral_flux: float, current_time: float, energy: float = 0.0) -> Tuple[bool, float]:
        self.onset_history.append(spectral_flux)
        self.onset_envelope.append(spectral_flux)
        self.energy_history.append(energy)
        
        if energy > 0.03:
            self.high_energy_frames += 1
        else:
            self.high_energy_frames = max(0, self.high_energy_frames - 1)
        
        if len(self.onset_history) < 5:
            return False, 0.0
        
        recent_flux = np.array(list(self.onset_history))
        flux_mean = float(np.mean(recent_flux))
        flux_std = float(np.std(recent_flux)) + 0.001
        flux_threshold = flux_mean + flux_std * self.onset_threshold_factor
        
        is_flux_peak = (spectral_flux > flux_threshold and 
                        len(recent_flux) >= 3 and
                        spectral_flux >= recent_flux[-2])
        
        is_energy_peak = False
        if len(self.energy_history) >= 5:
            recent_energy = np.array(list(self.energy_history))
            energy_mean = float(np.mean(recent_energy))
            energy_std = float(np.std(recent_energy)) + 0.001
            energy_threshold = energy_mean + energy_std * self.energy_threshold_factor
            
            is_energy_peak = (energy > energy_threshold and
                              len(recent_energy) >= 3 and
                              energy >= recent_energy[-2])
        
        is_peak = is_flux_peak or is_energy_peak
        
        is_fallback_beat = False
        if self.high_energy_frames > 10 and not is_peak:
            fallback_bpm = self.current_bpm if self.current_bpm > 0 else 120
            fallback_interval = 60.0 / fallback_bpm
            if current_time - self.last_fallback_beat >= fallback_interval * 0.9:
                is_fallback_beat = True
                self.last_fallback_beat = current_time
        
        time_since_last = current_time - self.last_beat_time
        
        is_beat = (is_peak or is_fallback_beat) and time_since_last >= self.min_beat_interval
        
        if is_beat:
            self.beat_times.append(current_time)
            self.last_beat_time = current_time
        
        onset_strength = min(100, (spectral_flux / (flux_threshold + 1e-6)) * 50)
        
        return is_beat, onset_strength
    
    def estimate_bpm(self) -> Tuple[int, int]:
        bpm_estimates = []
        confidences = []
        
        if len(self.beat_times) >= 4:
            intervals = np.diff(np.array(self.beat_times, dtype=np.float64))
            valid_intervals = intervals[
                (intervals > 60.0 / self.max_bpm) & 
                (intervals < 60.0 / self.min_bpm)
            ]
            
            if len(valid_intervals) >= 2:
                median_interval = float(np.median(valid_intervals))
                if median_interval > 0:
                    bpm_from_intervals = 60.0 / median_interval
                    rel_std = float(np.std(valid_intervals) / (np.mean(valid_intervals) + 1e-9))
                    interval_confidence = max(0, 100 - rel_std * 150)
                    bpm_estimates.append(bpm_from_intervals)
                    confidences.append(interval_confidence)
        
        if len(self.onset_envelope) >= 100:
            onset_array = np.array(list(self.onset_envelope), dtype=np.float32)
            onset_array = onset_array - np.mean(onset_array)
            norm = float(np.sqrt(np.sum(onset_array ** 2)))
            if norm > 0:
                onset_array = onset_array / norm
            
            autocorr = np.correlate(onset_array, onset_array, mode='full')
            autocorr = autocorr[len(autocorr) // 2:]
            
            frames_per_second = self.sr / self.hop_size
            min_lag = int(frames_per_second * 60 / self.max_bpm)
            max_lag = int(frames_per_second * 60 / self.min_bpm)
            max_lag = min(max_lag, len(autocorr) - 1)
            
            if max_lag > min_lag:
                search_region = autocorr[min_lag:max_lag]
                if len(search_region) > 0:
                    peak_idx = np.argmax(search_region) + min_lag
                    if peak_idx > 0:
                        bpm_from_autocorr = frames_per_second * 60 / peak_idx
                        peak_value = float(autocorr[peak_idx])
                        autocorr_confidence = max(0, min(100, peak_value * 120))
                        bpm_estimates.append(bpm_from_autocorr)
                        confidences.append(autocorr_confidence)
        
        if not bpm_estimates:
            # Check if beats are recent (within last 2 seconds)
            current_time = time.time()
            recent_beats = [t for t in self.beat_times if current_time - t < 2.0]
            if len(recent_beats) >= 2:
                return self.current_bpm if self.current_bpm > 0 else 120, 10  # Lower confidence
            # No recent beats - return 0 confidence to help stop detection
            return self.current_bpm if self.current_bpm > 0 else 0, 0
        
        total_conf = sum(confidences)
        if total_conf > 0:
            weighted_bpm = sum(b * c for b, c in zip(bpm_estimates, confidences)) / total_conf
        else:
            weighted_bpm = np.mean(bpm_estimates)
        
        final_bpm = int(round(max(self.min_bpm, min(self.max_bpm, weighted_bpm))))
        
        if self.current_bpm > 0:
            ratio = final_bpm / self.current_bpm
            if 1.8 < ratio < 2.2:
                final_bpm = final_bpm // 2
            elif 0.45 < ratio < 0.55:
                final_bpm = final_bpm * 2
        
        self.bpm_history.append(final_bpm)
        smoothed_bpm = int(round(np.median(list(self.bpm_history))))
        
        final_confidence = int(min(100, max(confidences) * 1.2)) if confidences else 0
        
        self.current_bpm = smoothed_bpm
        self.bpm_confidence = final_confidence
        
        return smoothed_bpm, final_confidence


# ============================================================
# 9. MUSIC DETECTION
# ============================================================

class MusicDetector:
    """Distinguish music from speech and noise"""

    def __init__(self):
        self.score_history = collections.deque(maxlen=12)  # Reduced from 30 for faster response (~280ms)
        self.is_music = False
        self.confidence = 0
        self.low_score_count = 0  # Track consecutive low scores for fast dropout
    
    def update(self, features: AudioFeatures, bpm_confidence: int, 
               beat_regularity: float) -> Tuple[bool, int]:
        score = 0.0
        
        if features.spectral_flatness < 0.35:
            score += 2.0
        elif features.spectral_flatness < 0.50:
            score += 1.0
        
        bass_total = features.energy_sub_bass + features.energy_bass
        if bass_total > 0.1:
            score += 1.5
        
        if 300 < features.spectral_centroid < 4000:
            score += 1.0
        
        if bpm_confidence > 40:
            score += 2.5
        elif bpm_confidence > 20:
            score += 1.5
        elif bpm_confidence > 5:
            score += 0.8
        
        if features.zero_crossing_rate < 0.15:
            score += 1.0
        
        if features.rms_energy > 0.01:
            score += 0.5
        
        self.score_history.append(score)
        avg_score = float(np.mean(list(self.score_history)))

        # Fast dropout: if current score is very low, exit music state quickly
        if score < 1.5:
            self.low_score_count += 1
        else:
            self.low_score_count = 0

        # Quick exit if we get 5+ consecutive low scores (~115ms)
        if self.low_score_count >= 5:
            self.is_music = False
            self.confidence = 0
            return self.is_music, self.confidence

        if self.is_music:
            threshold = 2.0  # Lower threshold to exit faster (was 2.5)
        else:
            threshold = 3.5

        self.is_music = avg_score >= threshold
        self.confidence = int(min(100, avg_score * 15))

        return self.is_music, self.confidence

    def reset(self):
        """Clear history for fresh start"""
        self.score_history.clear()
        self.is_music = False
        self.confidence = 0
        self.low_score_count = 0


class AudioClassifier:
    """Classify audio into SILENCE/NOISE/SPEECH/MUSIC"""

    def __init__(self):
        self.history = collections.deque(maxlen=10)  # Reduced from 30 for faster response (~230ms)
        self.current_type = "SILENCE"
        self.confidence = 0
        self.silence_streak = 0  # Track consecutive silence detections
    
    def classify(self, features: AudioFeatures, bpm_confidence: int, 
                 noise_floor: float) -> Tuple[str, int]:
        scores = {
            "SILENCE": 0.0,
            "NOISE": 0.0,
            "SPEECH": 0.0,
            "MUSIC": 0.0
        }
        
        energy_above_noise = features.rms_energy - noise_floor
        
        if energy_above_noise < 0.005:
            scores["SILENCE"] = 90.0
        elif energy_above_noise < 0.015:
            scores["SILENCE"] = 50.0
        elif energy_above_noise < 0.03:
            scores["SILENCE"] = 20.0
        
        if features.spectral_flatness > 0.6:
            scores["NOISE"] += 40.0
        elif features.spectral_flatness > 0.4:
            scores["NOISE"] += 25.0
        
        if features.spectral_centroid > 5000 or features.spectral_centroid < 200:
            scores["NOISE"] += 20.0
        
        if features.zero_crossing_rate > 0.15 and features.spectral_flatness > 0.4:
            scores["NOISE"] += 20.0
        
        if 0.04 < features.zero_crossing_rate < 0.18:
            scores["SPEECH"] += 25.0
        
        if 300 < features.spectral_centroid < 3500:
            scores["SPEECH"] += 20.0
        
        bass_total = features.energy_sub_bass + features.energy_bass
        if bass_total < 0.2:
            scores["SPEECH"] += 15.0
        
        if bpm_confidence < 30:
            scores["SPEECH"] += 20.0
        
        if 0.2 < features.spectral_flatness < 0.5:
            scores["SPEECH"] += 15.0
        
        if bpm_confidence > 40:
            scores["MUSIC"] += 35.0
        elif bpm_confidence > 20:
            scores["MUSIC"] += 25.0
        elif bpm_confidence > 10:
            scores["MUSIC"] += 15.0
        elif bpm_confidence > 0:
            scores["MUSIC"] += 8.0
        
        if features.spectral_flatness < 0.35:
            scores["MUSIC"] += 25.0
        elif features.spectral_flatness < 0.45:
            scores["MUSIC"] += 15.0
        
        if bass_total > 0.1:
            scores["MUSIC"] += 20.0
        
        if features.zero_crossing_rate < 0.12:
            scores["MUSIC"] += 15.0
        
        if energy_above_noise > 0.02:
            scores["MUSIC"] += 15.0
            scores["SPEECH"] += 10.0
        
        self.history.append(scores.copy())

        # Fast silence detection: if silence score is very high, switch immediately
        if scores["SILENCE"] >= 70:
            self.silence_streak += 1
        else:
            self.silence_streak = 0

        # Quick switch to SILENCE after 3 consecutive high-silence frames (~70ms)
        if self.silence_streak >= 3:
            self.current_type = "SILENCE"
            self.confidence = int(scores["SILENCE"])
            return self.current_type, self.confidence

        avg_scores = {k: 0.0 for k in scores}
        for h in self.history:
            for k in h:
                avg_scores[k] += h[k]
        for k in avg_scores:
            avg_scores[k] /= len(self.history)

        winner = max(avg_scores, key=avg_scores.get)
        winner_score = avg_scores[winner]

        sorted_scores = sorted(avg_scores.values(), reverse=True)
        margin = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        confidence = int(min(100, winner_score + margin))

        # Reduced hysteresis margin for faster state changes (was 15)
        if winner != self.current_type:
            if winner_score > avg_scores.get(self.current_type, 0) + 8:
                self.current_type = winner

        self.confidence = confidence
        return self.current_type, confidence

    def reset(self):
        """Clear history for fresh start"""
        self.history.clear()
        self.current_type = "SILENCE"
        self.confidence = 0
        self.silence_streak = 0


# ============================================================
# 10. DANCE ENGINE (with eye integration)
# ============================================================

class DanceMove:
    def __init__(self, name: str, direction: str, duration_beats: float, 
                 intensity: float = 1.0, asymmetry: float = 0.0):
        self.name = name
        self.direction = direction
        self.duration_beats = duration_beats
        self.intensity = intensity
        self.asymmetry = asymmetry

class DancePattern:
    def __init__(self, name: str, moves: List[DanceMove], energy_level: str = "medium"):
        self.name = name
        self.moves = moves
        self.energy_level = energy_level
        self.length = sum(m.duration_beats for m in moves)

class AdvancedDanceEngine:
    """Human-like dance engine with eye coordination"""
    
    def __init__(self):
        self._build_move_library()
        self._build_patterns()
        self._build_transitions()
        
        self.current_pattern = None
        self.pattern_index = 0
        self.beats_in_pattern = 0
        self.patterns_completed = 0
        self.last_style = None
        self.last_energy = "medium"
        self.last_beat_time = 0
        self.beat_count = 0
        self.recent_patterns = collections.deque(maxlen=4)
        
        # Track spins for dizzy eyes
        self.spin_count = 0
        self.last_spin_time = 0
        
    def _build_move_library(self):
        self.moves = {
            "pulse_forward": DanceMove("pulse_forward", "forward", 1.0, 1.0),
            "pulse_back": DanceMove("pulse_back", "backward", 1.0, 1.0),
            "pulse_left": DanceMove("pulse_left", "left", 1.0, 1.0),
            "pulse_right": DanceMove("pulse_right", "right", 1.0, 1.0),
            "step_forward": DanceMove("step_forward", "forward", 1.5, 1.0),
            "step_back": DanceMove("step_back", "backward", 1.5, 1.0),
            "step_left": DanceMove("step_left", "left", 1.5, 1.0),
            "step_right": DanceMove("step_right", "right", 1.5, 1.0),
            "thrust_forward": DanceMove("thrust_forward", "forward", 1.5, 1.0),
            "thrust_back": DanceMove("thrust_back", "backward", 1.5, 1.0),
            "spin_quarter": DanceMove("spin_quarter", "spin", 1.0, 1.0),
            "spin_half": DanceMove("spin_half", "spin", 1.5, 1.0),
            "spin_full": DanceMove("spin_full", "spin", 2.5, 1.0),
            "spin_360": DanceMove("spin_360", "spin", 3.5, 1.0),
            "hold_short": DanceMove("hold", "stop", 0.5, 0.0),
            "hold_beat": DanceMove("hold", "stop", 1.0, 0.0),
            "wiggle_lr": DanceMove("wiggle", "left", 0.5, 1.0),
            "wiggle_fb": DanceMove("wiggle", "forward", 0.5, 1.0),
        }
    
    def _build_patterns(self):
        m = self.moves
        
        self.patterns = {
            "sway": DancePattern("Sway", [
                m["pulse_left"], m["hold_short"],
                m["pulse_right"], m["hold_short"],
                m["pulse_left"], m["hold_short"],
                m["pulse_right"], m["hold_short"],
            ], "low"),
            
            "slow_groove": DancePattern("Slow Groove", [
                m["step_forward"], m["hold_beat"],
                m["step_back"], m["hold_beat"],
            ], "low"),
            
            "dreamy": DancePattern("Dreamy", [
                m["pulse_forward"], m["pulse_left"],
                m["pulse_back"], m["pulse_right"],
                m["hold_beat"], m["spin_quarter"],
            ], "low"),
            
            "basic_bounce": DancePattern("Basic Bounce", [
                m["pulse_forward"], m["pulse_back"],
                m["pulse_forward"], m["pulse_back"],
                m["pulse_forward"], m["pulse_back"],
                m["step_left"], m["step_right"],
            ], "medium"),
            
            "side_step": DancePattern("Side Step", [
                m["step_left"], m["pulse_forward"],
                m["step_right"], m["pulse_forward"],
                m["step_left"], m["pulse_back"],
                m["step_right"], m["pulse_back"],
            ], "medium"),
            
            "groove_box": DancePattern("Groove Box", [
                m["step_forward"], m["step_right"],
                m["step_back"], m["step_left"],
            ], "medium"),
            
            "shuffle": DancePattern("Shuffle", [
                m["pulse_left"], m["pulse_right"],
                m["pulse_left"], m["pulse_right"],
                m["pulse_forward"], m["pulse_forward"],
                m["pulse_back"], m["pulse_back"],
            ], "medium"),
            
            "pump": DancePattern("Pump", [
                m["thrust_forward"], m["pulse_back"],
                m["thrust_forward"], m["pulse_back"],
                m["thrust_forward"], m["thrust_back"],
                m["spin_quarter"], m["spin_quarter"],
            ], "high"),
            
            "strobe": DancePattern("Strobe", [
                m["pulse_forward"], m["pulse_back"],
                m["pulse_forward"], m["pulse_back"],
                m["pulse_left"], m["pulse_right"],
                m["pulse_left"], m["pulse_right"],
            ], "high"),
            
            "hyperdrive": DancePattern("Hyperdrive", [
                m["thrust_forward"], m["spin_quarter"],
                m["thrust_back"], m["spin_quarter"],
                m["thrust_forward"], m["spin_quarter"],
                m["thrust_back"], m["spin_quarter"],
            ], "high"),
            
            "bass_drop": DancePattern("Bass Drop", [
                m["thrust_forward"], m["hold_short"],
                m["thrust_forward"], m["hold_short"],
                m["thrust_back"], m["thrust_back"],
                m["hold_beat"],
            ], "high"),
            
            "head_bang": DancePattern("Head Bang", [
                m["thrust_forward"], m["pulse_back"],
                m["thrust_forward"], m["pulse_back"],
                m["thrust_forward"], m["pulse_back"],
                m["thrust_forward"], m["pulse_back"],
            ], "high"),
            
            "float": DancePattern("Float", [
                m["pulse_left"], m["pulse_forward"],
                m["pulse_right"], m["pulse_forward"],
                m["spin_quarter"], m["hold_short"],
                m["spin_quarter"], m["hold_short"],
            ], "medium"),
            
            "twirl": DancePattern("Twirl", [
                m["spin_half"], m["pulse_forward"],
                m["spin_half"], m["pulse_back"],
            ], "medium"),
        }
        
        self.pattern_categories = {
            "slow": ["sway", "slow_groove", "dreamy"],
            "medium": ["basic_bounce", "side_step", "groove_box", "shuffle"],
            "fast": ["pump", "strobe", "hyperdrive"],
            "bass": ["bass_drop", "head_bang", "basic_bounce", "pump"],
            "treble": ["float", "twirl", "sway", "shuffle"],
            "mid": ["side_step", "groove_box", "basic_bounce"],
        }
    
    def _build_transitions(self):
        m = self.moves
        
        self.transitions = [
            DancePattern("Spin!", [m["spin_360"]], "medium"),
            DancePattern("Double Spin!", [m["spin_full"], m["spin_full"]], "high"),
            DancePattern("Quick Turn", [m["spin_half"], m["hold_short"]], "medium"),
            DancePattern("Thrust Spin", [m["thrust_forward"], m["spin_full"]], "high"),
            DancePattern("Reverse Spin", [m["step_back"], m["spin_full"]], "medium"),
            DancePattern("Pause", [m["hold_beat"], m["hold_beat"]], "low"),
        ]
    
    def reset(self):
        self.current_pattern = None
        self.pattern_index = 0
        self.beats_in_pattern = 0
        self.patterns_completed = 0
        self.last_beat_time = 0
        self.beat_count = 0
        self.recent_patterns.clear()
        self.spin_count = 0
    
    def _get_style_from_bpm(self, bpm: int) -> str:
        if bpm <= 0:
            return "medium"
        if bpm < 95:
            return "slow"
        if bpm < 130:
            return "medium"
        return "fast"
    
    def _get_energy_level(self, energy_0_100: int) -> str:
        if energy_0_100 < 35:
            return "low"
        if energy_0_100 < 70:
            return "medium"
        return "high"
    
    def _select_pattern(self, bpm: int, energy_0_100: int, dominant_band: str) -> DancePattern:
        style = self._get_style_from_bpm(bpm)
        energy = self._get_energy_level(energy_0_100)
        
        candidates = set(self.pattern_categories.get(style, []))
        
        if dominant_band in self.pattern_categories:
            band_patterns = self.pattern_categories[dominant_band]
            candidates.update(band_patterns)
        
        for recent in self.recent_patterns:
            candidates.discard(recent)
        
        if len(candidates) < 2:
            candidates = set(self.pattern_categories.get(style, ["basic_bounce"]))
        
        candidate_list = list(candidates)
        
        weighted = []
        for name in candidate_list:
            pattern = self.patterns.get(name)
            if pattern:
                weight = 1.0
                if pattern.energy_level == energy:
                    weight = 3.0
                elif (pattern.energy_level == "medium"):
                    weight = 2.0
                weighted.extend([name] * int(weight * 2))
        
        if weighted:
            chosen_name = random.choice(weighted)
        else:
            chosen_name = "basic_bounce"
        
        self.recent_patterns.append(chosen_name)
        return self.patterns[chosen_name]
    
    def _select_transition(self, energy_0_100: int) -> DancePattern:
        energy = self._get_energy_level(energy_0_100)
        
        if energy == "high":
            candidates = [t for t in self.transitions if t.energy_level in ["high", "medium"]]
        elif energy == "low":
            candidates = [t for t in self.transitions if t.energy_level in ["low", "medium"]]
        else:
            candidates = self.transitions
        
        return random.choice(candidates) if candidates else self.transitions[0]
    
    def next_move(self, bpm: int, energy_0_100: int, dominant_band: str, is_beat: bool = True):
        """Get the next dance move. Returns: (direction, duration, left_dc, right_dc, is_spin)"""
        style = self._get_style_from_bpm(bpm)
        
        bpm_eff = max(60, min(180, bpm if bpm > 0 else 120))
        beat_duration = 60.0 / bpm_eff
        
        need_new_pattern = (
            self.current_pattern is None or
            self.pattern_index >= len(self.current_pattern.moves)
        )
        
        if need_new_pattern:
            do_transition = (
                self.patterns_completed > 0 and 
                self.patterns_completed % random.randint(2, 4) == 0
            )
            
            if do_transition and self.current_pattern and "Spin" not in self.current_pattern.name:
                self.current_pattern = self._select_transition(energy_0_100)
                self.pattern_index = 0
            else:
                self.current_pattern = self._select_pattern(bpm, energy_0_100, dominant_band)
                self.pattern_index = 0
                self.patterns_completed += 1
        
        move = self.current_pattern.moves[self.pattern_index]
        self.pattern_index += 1
        
        duration = move.duration_beats * beat_duration
        
        if style == "fast":
            duration *= 0.85
        elif style == "slow":
            duration *= 1.1
        
        duration = max(0.15, min(2.5, duration))
        
        if move.direction == "stop":
            power = 0
        else:
            power = 100
        
        asymmetry_factor = 1.0 + (move.asymmetry * 0.15)
        l_dc = power
        r_dc = power * asymmetry_factor
        
        l_dc *= random.uniform(0.98, 1.0)
        r_dc *= random.uniform(0.98, 1.0)
        l_dc = max(0, min(100, l_dc))
        r_dc = max(0, min(100, r_dc))
        
        # Track spins
        is_spin = move.direction == "spin"
        if is_spin:
            self.spin_count += 1
            self.last_spin_time = time.time()
        
        with state_lock:
            ui_state["dominant_band"] = dominant_band
            ui_state["style"] = style
            ui_state["pattern_name"] = self.current_pattern.name
            ui_state["step_info"] = f"{move.name}"
        
        return move.direction, duration, l_dc, r_dc, is_spin


# Create the advanced dance engine
dance_engine = AdvancedDanceEngine()

# ============================================================
# 11. MOTOR WORKER (with eye coordination)
# ============================================================
def motor_worker():
    while not stop_event.is_set():
        try:
            item = motor_q.get(timeout=0.1)
            # Unpack - may have 4 or 5 elements depending on version
            if len(item) == 5:
                direction, duration, l_dc, r_dc, is_spin = item
            else:
                direction, duration, l_dc, r_dc = item
                is_spin = direction == "spin"
        except queue.Empty:
            continue

        # Update eyes based on movement direction
        robot_eyes.on_movement(direction)
        
        drive(direction, l_dc, r_dc)
        start = time.time()

        while (time.time() - start) < float(duration):
            if stop_event.is_set():
                break
            with state_lock:
                auto = autonomous_mode
            if not auto:
                break
            time.sleep(0.01)

        drive("stop")
        
        # Show dizzy eyes after spin
        if is_spin and duration > 1.5:
            # Long spin = dizzy!
            threading.Thread(target=robot_eyes.on_spin_complete, daemon=True).start()
        
        motor_q.task_done()


threading.Thread(target=motor_worker, daemon=True).start()

# ============================================================
# 12. AUDIO LOOP (with eye integration)
# ============================================================
def audio_loop():
    print("Audio online (with LED eyes).")
    
    analyzer = AudioAnalyzer(SAMPLE_RATE, CHUNK_SIZE)
    beat_detector = BeatDetector(SAMPLE_RATE, HOP_SIZE)
    music_detector = MusicDetector()
    audio_classifier = AudioClassifier()
    
    energy_hist = collections.deque(maxlen=25)
    spectrum_smooth = np.zeros(32, dtype=np.float32)
    
    is_dancing = False
    silence_counter = 0
    last_move_time = 0
    
    # Eye-related state
    last_eye_update = 0
    eye_update_interval = 0.05  # Update eyes every 50ms
    
    # Special animation triggers
    consecutive_high_energy_beats = 0
    last_special_animation = 0
    
    audio_buffer = np.zeros(CHUNK_SIZE, dtype=np.float32)
    
    while not stop_event.is_set():
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                channels=1,
                dtype="float32",
            ) as stream:

                with state_lock:
                    ui_state["audio_ok"] = True
                    ui_state["status"] = "Waiting..."

                while not stop_event.is_set():
                    data, overflowed = stream.read(CHUNK_SIZE)
                    x = data[:, 0].copy()
                    
                    with state_lock:
                        sens = int(ui_state["sensitivity"])
                        gain_setting = int(ui_state.get("gain", 50))
                    
                    if gain_setting == 0:
                        gain = 0.0
                        is_muted = True
                    else:
                        gain = 10 ** ((gain_setting - 50) / 50)
                        is_muted = False
                    
                    noise_gate_threshold = 0.1 * (10 ** (-sens / 50))
                    
                    x_gained = x * gain
                    raw_rms = float(np.sqrt(np.mean(x_gained ** 2)))
                    is_below_gate = raw_rms < noise_gate_threshold
                    
                    if is_muted or is_below_gate:
                        x_gained = np.zeros_like(x_gained)
                    
                    current_time = time.time()
                    features = analyzer.analyze(x_gained, 1.0)
                    
                    if not is_muted and features.rms_energy < analyzer.noise_floor * 1.5:
                        analyzer.calibrate_noise_floor(x * gain)
                    
                    is_beat, onset_strength = beat_detector.detect_beat(
                        features.spectral_flux, current_time, features.rms_energy
                    )
                    
                    bpm, bpm_confidence = beat_detector.estimate_bpm()
                    beat_regularity = bpm_confidence / 100.0
                    
                    is_music, music_confidence = music_detector.update(
                        features, bpm_confidence, beat_regularity
                    )
                    
                    audio_type, audio_type_confidence = audio_classifier.classify(
                        features, bpm_confidence, analyzer.noise_floor
                    )
                    
                    energy_above_noise = max(0, features.rms_energy - analyzer.noise_floor * 0.5)
                    energy_normalized = energy_above_noise / (0.2 + analyzer.noise_floor)
                    energy_0_100 = int(min(100, energy_normalized * 150))
                    
                    energy_hist.append(energy_0_100)
                    
                    bass_energy = int(min(100, (features.energy_sub_bass + features.energy_bass) * 200))
                    mid_energy = int(min(100, (features.energy_low_mid + features.energy_mid + features.energy_high_mid) * 150))
                    treble_energy = int(min(100, features.energy_treble * 300))
                    
                    start_thresh = 40.0 - (sens * 0.35)
                    silence_thresh = start_thresh * 0.4
                    
                    # Generate waveform data
                    if is_muted or is_below_gate:
                        waveform_data = [0] * 64
                    else:
                        waveform_data = []
                        step = len(x_gained) // 64
                        for i in range(64):
                            idx = i * step
                            chunk = x_gained[idx:idx+step]
                            abs_chunk = np.abs(chunk)
                            max_idx = np.argmax(abs_chunk)
                            peak_sample = float(chunk[max_idx])
                            scale_factor = 80 + (sens * 1.2)
                            scaled_value = peak_sample * scale_factor
                            waveform_data.append(int(max(-100, min(100, scaled_value))))
                    
                    # Generate spectrum data
                    if is_muted or is_below_gate:
                        spectrum_data = [0] * 32
                    else:
                        windowed = (x_gained * np.hanning(len(x_gained))).astype(np.float32)
                        fft_result = np.fft.rfft(windowed)
                        mag = np.abs(fft_result)
                        
                        sorted_mag = np.sort(mag)
                        spectrum_noise_floor = float(sorted_mag[len(sorted_mag) // 4]) * 1.5
                        
                        spectrum_data = []
                        num_bins = len(mag)
                        max_mag = float(np.percentile(mag, 90))
                        
                        for i in range(32):
                            freq_lo = 20 * (2 ** (i * 10 / 32))
                            freq_hi = 20 * (2 ** ((i + 1) * 10 / 32))
                            bin_lo = int(freq_lo * num_bins * 2 / SAMPLE_RATE)
                            bin_hi = int(freq_hi * num_bins * 2 / SAMPLE_RATE)
                            bin_lo = max(0, min(bin_lo, num_bins - 1))
                            bin_hi = max(bin_lo + 1, min(bin_hi, num_bins))
                            
                            band_slice = mag[bin_lo:bin_hi]
                            band_max = float(np.max(band_slice))
                            band_mean = float(np.mean(band_slice))
                            band_mag = band_max * 0.6 + band_mean * 0.4
                            band_above_noise = max(0, band_mag - spectrum_noise_floor)
                            
                            if max_mag > spectrum_noise_floor * 1.2:
                                scale_factor = 65 + (sens * 1.0)
                                dynamic_range = max_mag - spectrum_noise_floor + 0.001
                                normalized = band_above_noise / dynamic_range
                                compressed = (normalized ** 0.6) if normalized > 0 else 0
                                value = int(min(100, compressed * scale_factor))
                            else:
                                value = 0
                            
                            spectrum_data.append(value)
                        
                        for i in range(32):
                            target = spectrum_data[i]
                            if target > spectrum_smooth[i]:
                                spectrum_smooth[i] += (target - spectrum_smooth[i]) * 0.5
                            else:
                                spectrum_smooth[i] += (target - spectrum_smooth[i]) * 0.2
                        
                        spectrum_data = [int(v) for v in spectrum_smooth]
                    
                    # ==========================================
                    # EYE UPDATES
                    # ==========================================
                    
                    # React to beats
                    if is_beat and not is_muted:
                        robot_eyes.on_beat(onset_strength, energy_0_100, bpm)
                        
                        # Track high energy beats for special animations
                        if energy_0_100 > 70:
                            consecutive_high_energy_beats += 1
                        else:
                            consecutive_high_energy_beats = 0
                        
                        # Trigger special animations occasionally
                        if consecutive_high_energy_beats >= 4 and current_time - last_special_animation > 5.0:
                            # Random special animation
                            specials = ["heart", "star", "happy"]
                            robot_eyes.trigger_special(random.choice(specials))
                            last_special_animation = current_time
                            consecutive_high_energy_beats = 0
                    
                    # Regular eye updates (idle behavior, expression based on energy)
                    if current_time - last_eye_update >= eye_update_interval:
                        robot_eyes.update_idle(energy_0_100, is_music)
                        if not robot_eyes.is_reacting_to_beat:
                            robot_eyes.set_energy_expression(energy_0_100, is_dancing)
                        last_eye_update = current_time
                    
                    # Update UI state
                    with state_lock:
                        ui_state["energy"] = energy_0_100
                        ui_state["energy_bass"] = bass_energy
                        ui_state["energy_mid"] = mid_energy
                        ui_state["energy_treble"] = treble_energy
                        ui_state["is_beat"] = is_beat if not is_muted else False
                        ui_state["beat_strength"] = int(onset_strength) if not is_muted else 0
                        ui_state["bpm"] = bpm
                        ui_state["bpm_confidence"] = bpm_confidence if not is_muted else 0
                        ui_state["music_gate"] = is_music if not is_muted else False
                        ui_state["music_confidence"] = music_confidence if not is_muted else 0
                        ui_state["dominant_band"] = features.dominant_band
                        ui_state["audio_type"] = audio_type if not is_muted else "MUTED"
                        ui_state["audio_type_confidence"] = audio_type_confidence if not is_muted else 0
                        ui_state["is_muted"] = is_muted
                        ui_state["noise_gate_threshold"] = int(noise_gate_threshold * 1000)
                        ui_state["is_gated"] = is_below_gate and not is_muted
                        ui_state["waveform"] = waveform_data
                        ui_state["spectrum"] = spectrum_data
                        ui_state["data_timestamp"] = int(current_time * 1000)
                        ui_state["noise_floor"] = int(analyzer.noise_floor * 1000)
                        ui_state["spectral_flux"] = int(min(100, features.spectral_flux))
                        ui_state["spectral_flatness"] = int(features.spectral_flatness * 100)
                        ui_state["spectral_centroid"] = int(features.spectral_centroid)
                        ui_state["onset_strength"] = int(onset_strength)
                        ui_state["zero_crossing_rate"] = int(features.zero_crossing_rate * 100)
                        ui_state["rhythm_regularity"] = bpm_confidence
                        ui_state["raw_rms"] = int(raw_rms * 1000)
                        
                        # Eye state for UI
                        eye_state = robot_eyes.get_state()
                        ui_state["eye_expression"] = eye_state["expression"]
                        ui_state["eye_enabled"] = eye_state["enabled"]
                        ui_state["eye_reacting"] = eye_state["is_reacting"]
                    
                    # Autonomous control
                    with state_lock:
                        auto = autonomous_mode

                    if auto:
                        if is_dancing:
                            # IMPROVED: Multi-signal silence detection for faster response
                            # Combine energy, gate, and classifier signals
                            LOW_ENERGY_THRESHOLD = 12  # Slightly raised for better detection
                            MEDIUM_ENERGY_THRESHOLD = 25

                            # Fast path: if below noise gate, increment quickly
                            if is_below_gate or is_muted:
                                silence_counter += 2.0  # Fast increment when gated
                            elif energy_0_100 < LOW_ENERGY_THRESHOLD:
                                silence_counter += 1.5  # Quick increment for very low energy
                            elif energy_0_100 < MEDIUM_ENERGY_THRESHOLD:
                                # Check audio type for additional signal
                                if audio_type == "SILENCE":
                                    silence_counter += 1.2
                                elif audio_type != "MUSIC":
                                    silence_counter += 0.8
                                else:
                                    silence_counter += 0.3
                            elif not is_music and audio_type != "MUSIC":
                                # Energy OK but classifiers say no music
                                silence_counter += 0.5
                            else:
                                # Clear music signal - reset counter
                                silence_counter = max(0, silence_counter - 1)  # Gradual reset

                            # Stop after ~350ms of accumulated silence signals (was 500ms)
                            # 15 frames at ~23ms = ~345ms
                            if silence_counter >= 15:
                                is_dancing = False
                                clear_motor_queue()
                                stop_robot()
                                last_move_time = 0
                                silence_counter = 0  # Reset for next time

                                # Reset classifiers for fresh start next time
                                music_detector.reset()
                                audio_classifier.reset()

                                # Eyes go sleepy
                                robot_eyes.set_expression("sleepy")

                                with state_lock:
                                    ui_state["status"] = "Waiting for music..."
                                    ui_state["pattern_name"] = "---"
                                    ui_state["step_info"] = "Ready"
                                    ui_state["style"] = "none"
                                    ui_state["is_dancing"] = False
                                    ui_state["current_move"] = "none"
                                    ui_state["dance_reason"] = "Music stopped"
                            else:
                                # Continue dancing - execute moves
                                bpm_for_timing = bpm if bpm > 0 else 120
                                beat_interval = 60.0 / max(60, min(180, bpm_for_timing))
                                time_since_last_move = current_time - last_move_time
                                
                                timer_trigger = time_since_last_move >= beat_interval * 0.7
                                should_move = is_beat or timer_trigger
                                
                                with state_lock:
                                    ui_state["debug_beat"] = is_beat
                                    ui_state["debug_timer"] = timer_trigger
                                    ui_state["debug_interval"] = round(beat_interval, 3)
                                    ui_state["debug_since_move"] = round(time_since_last_move, 3)
                                
                                if should_move:
                                    last_move_time = current_time
                                    
                                    if is_beat:
                                        flash_led()
                                    
                                    move, dur, l_dc, r_dc, is_spin = dance_engine.next_move(
                                        bpm=bpm,
                                        energy_0_100=energy_0_100,
                                        dominant_band=features.dominant_band,
                                    )

                                    try:
                                        motor_q.put_nowait((move, dur, l_dc, r_dc, is_spin))
                                    except queue.Full:
                                        pass
                                    
                                    with state_lock:
                                        ui_state["status"] = "🎵 DANCING!"
                                        ui_state["is_dancing"] = True
                                        ui_state["current_move"] = move
                                        ui_state["move_intensity"] = int((l_dc + r_dc) / 2)
                                        ui_state["patterns_completed"] = dance_engine.patterns_completed
                                        ui_state["dance_reason"] = ""
                                        ui_state["beat_triggered"] = is_beat
                        else:
                            can_start = energy_0_100 > start_thresh
                            has_music = is_music or audio_type == "MUSIC"
                            energy_suggests_music = energy_0_100 > 40 and features.spectral_flatness < 0.5
                            has_beat = bpm_confidence > 10 or bpm > 0 or len(beat_detector.beat_times) >= 2
                            high_energy_override = energy_0_100 > 50 and not is_muted and not is_below_gate
                            
                            with state_lock:
                                if not can_start and not high_energy_override:
                                    ui_state["dance_reason"] = f"Energy too low ({energy_0_100}% < {int(start_thresh)}%)"
                                elif not has_music and not energy_suggests_music and not high_energy_override:
                                    ui_state["dance_reason"] = f"No music detected (type: {audio_type})"
                                elif not has_beat and not high_energy_override:
                                    ui_state["dance_reason"] = f"No beat detected (BPM conf: {bpm_confidence}%)"
                                else:
                                    ui_state["dance_reason"] = ""
                            
                            should_start = (can_start and (has_music or energy_suggests_music) and has_beat) or high_energy_override
                            
                            if should_start:
                                is_dancing = True
                                silence_counter = 0
                                last_move_time = current_time
                                clear_motor_queue()
                                dance_engine.reset()
                                
                                # Eyes get excited!
                                robot_eyes.set_expression("excited")
                                
                                move, dur, l_dc, r_dc, is_spin = dance_engine.next_move(
                                    bpm=bpm if bpm > 0 else 120,
                                    energy_0_100=energy_0_100,
                                    dominant_band=features.dominant_band,
                                )
                                try:
                                    motor_q.put_nowait((move, dur, l_dc, r_dc, is_spin))
                                except queue.Full:
                                    pass
                                
                                with state_lock:
                                    ui_state["status"] = "🎶 Music Detected! Starting..."
                                    ui_state["is_dancing"] = True
                                    ui_state["current_move"] = move
                                    ui_state["move_intensity"] = int((l_dc + r_dc) / 2)
                                    ui_state["dance_reason"] = ""
                    else:
                        if is_dancing:
                            is_dancing = False
                            clear_motor_queue()
                            stop_robot()
                        silence_counter = 0
                        dance_engine.reset()
                        with state_lock:
                            ui_state["is_dancing"] = False
                            ui_state["current_move"] = "none"
                            ui_state["dance_reason"] = "Autonomous mode OFF"

        except Exception as e:
            with state_lock:
                ui_state["audio_ok"] = False
                ui_state["status"] = f"Audio error: {type(e).__name__}"
                ui_state["music_gate"] = False
                ui_state["is_beat"] = False
            time.sleep(0.75)


threading.Thread(target=audio_loop, daemon=True).start()

# ============================================================
# 13. WEB SERVER
# ============================================================
app = Flask(__name__, static_folder=None)
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def convert_to_json_serializable(obj):
    if isinstance(obj, dict):
        return {k: convert_to_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.bool_, np.bool8)):
        return bool(obj)
    elif isinstance(obj, (np.integer, np.int_, np.int8, np.int16, np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float_, np.float16, np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, np.str_):
        return str(obj)
    else:
        return obj


@app.route("/")
def index():
    html_path = os.path.join(SCRIPT_DIR, "robot_frontend.html")
    try:
        with open(html_path, 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html'}
    except FileNotFoundError:
        return "Error: robot_frontend.html not found in " + SCRIPT_DIR, 404


@app.route("/splat")
def splat():
    html_path = os.path.join(SCRIPT_DIR, "ravitto_splat.html")
    try:
        with open(html_path, 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html'}
    except FileNotFoundError:
        return "Error: ravitto_splat.html not found in " + SCRIPT_DIR, 404


@app.route("/studio")
def studio():
    html_path = os.path.join(SCRIPT_DIR, "ravitto_studio.html")
    try:
        with open(html_path, 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html'}
    except FileNotFoundError:
        return "Error: ravitto_studio.html not found in " + SCRIPT_DIR, 404


@app.route("/api/status", methods=["GET"])
def status():
    with state_lock:
        snap = dict(ui_state)
        snap["autonomous"] = autonomous_mode
    snap = convert_to_json_serializable(snap)
    return jsonify(snap)


@app.route("/api/control/<cmd>", methods=["POST"])
def control(cmd):
    global autonomous_mode

    if cmd == "toggle_auto":
        with state_lock:
            autonomous_mode = not autonomous_mode
            auto = autonomous_mode

        if auto:
            clear_motor_queue()
            dance_engine.reset()
            robot_eyes.set_expression("normal")
            with state_lock:
                ui_state["status"] = "Listening for music..."
                ui_state["is_dancing"] = False
                ui_state["current_move"] = "none"
                ui_state["dance_reason"] = "Waiting for music to start"
        else:
            clear_motor_queue()
            stop_robot()
            robot_eyes.set_expression("normal")
            with state_lock:
                ui_state["status"] = "Stopped"
                ui_state["pattern_name"] = "---"
                ui_state["step_info"] = "Ready"
                ui_state["dominant_band"] = "none"
                ui_state["style"] = "none"
                ui_state["is_dancing"] = False
                ui_state["current_move"] = "none"
                ui_state["move_intensity"] = 0
                ui_state["dance_reason"] = "Autonomous mode OFF"

    elif cmd in ["forward", "backward", "left", "right"]:
        with state_lock:
            autonomous_mode = False
            ui_state["is_dancing"] = False
            ui_state["current_move"] = cmd
        clear_motor_queue()
        drive(cmd, 100, 100)
        robot_eyes.on_movement(cmd)

    elif cmd == "stop":
        with state_lock:
            autonomous_mode = False
            ui_state["is_dancing"] = False
            ui_state["current_move"] = "none"
        clear_motor_queue()
        stop_robot()

    return "OK"


@app.route("/api/sens/<int:val>", methods=["POST"])
def set_sens(val):
    v = int(max(0, min(100, val)))
    with state_lock:
        ui_state["sensitivity"] = v
    return "OK"


@app.route("/api/gain/<int:val>", methods=["POST"])
def set_gain(val):
    v = int(max(0, min(100, val)))
    with state_lock:
        ui_state["gain"] = v
    return "OK"


# NEW: Eye control endpoints
@app.route("/api/eyes/<expression>", methods=["POST"])
def set_eyes(expression):
    """Manually set eye expression"""
    robot_eyes.set_expression(expression)
    return "OK"


@app.route("/api/eyes/special/<special_type>", methods=["POST"])
def trigger_eye_special(special_type):
    """Trigger a special eye animation"""
    robot_eyes.trigger_special(special_type)
    return "OK"


# ============================================================
# 14. MAIN
# ============================================================
if __name__ == "__main__":
    try:
        print("=" * 50)
        print("Robot Rave Controller with LED EYES!")
        print("=" * 50)
        
        # Run boot sequence for eyes
        if LED_AVAILABLE:
            print("\n[STARTUP] Running eye boot sequence...")
            robot_eyes.boot_sequence()
        else:
            print("\n[STARTUP] LED eyes not available - running without")
            robot_eyes.has_booted = True
        
        with state_lock:
            ui_state["status"] = "Waiting..."
        
        print("\nServer starting...")
        print("Frontend: http://localhost:5000/")
        print("API: http://localhost:5000/api/status")
        print("\nFeatures:")
        print("  - Beat-reactive LED eyes")
        print("  - Movement-synced eye direction")
        print("  - Special animations (hearts, stars, dizzy)")
        print("  - Natural blinking and idle behaviors")
        print("\nEye API endpoints:")
        print("  POST /api/eyes/<expression>  - Set expression")
        print("  POST /api/eyes/special/<type> - Trigger special animation")
        print("=" * 50)
        
        app.run(host="0.0.0.0", port=5000, debug=False)
        
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[SHUTDOWN] Cleaning up...")
        stop_event.set()
        
        # Sleep sequence for eyes
        if LED_AVAILABLE:
            robot_eyes.sleep_sequence()
        
        with state_lock:
            autonomous_mode = False
        clear_motor_queue()
        stop_robot()
        GPIO.cleanup()
        print("[SHUTDOWN] Complete!")
