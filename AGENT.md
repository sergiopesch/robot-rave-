# Agent Guidelines for Robot Rave

This document provides guidance for AI assistants (like Claude Code) working on this project.

## Project Overview

Robot Rave is a Raspberry Pi-based dancing robot that reacts to music. The project consists of:

- **Backend** (`robot_backend.py`): Flask server handling audio processing, motor control, LED eyes, and dance logic
- **Frontend** (`robot_frontend.html`): Web-based control interface served by the backend

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  USB Microphone │────>│  Audio Analysis  │────>│ Dance Engine│
└─────────────────┘     └──────────────────┘     └─────────────┘
                                                        │
┌─────────────────┐     ┌──────────────────┐           │
│   Web Browser   │<───>│   Flask Server   │<──────────┘
└─────────────────┘     └──────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
              ┌─────▼─────┐          ┌──────▼──────┐
              │  Motors   │          │  LED Eyes   │
              │ (GPIO PWM)│          │ (I2C HT16K33)│
              └───────────┘          └─────────────┘
```

## Key Components in robot_backend.py

### Audio Pipeline
1. `AudioAnalyzer` (line ~1090): Extracts features from audio chunks
2. `BeatDetector` (line ~1230): Detects beats and estimates BPM
3. `MusicDetector` (line ~1336): Classifies if audio is music
4. `AudioClassifier` (line ~1387): Classifies audio as SILENCE/NOISE/SPEECH/MUSIC

### Motor Control
- `drive()` function (line ~1024): Controls motor direction
- `motor_worker()` thread (line ~1817): Processes movement queue
- GPIO pins: Left motor (9, 10), Right motor (7, 8)

### Dance System
- `DanceMove` / `DancePattern` classes (line ~1492): Define dance moves
- `AdvancedDanceEngine` (line ~1506): Selects and sequences dance patterns

### LED Eyes
- `RobotEyes` class (line ~580): Manages LED matrix expressions
- Eye patterns defined in `EYE_PATTERNS` dictionary (line ~114)

### Main Loop
- `audio_loop()` function (line ~1860): Main audio processing and dance control loop
- Runs at ~43 Hz (23ms per frame based on 2048 sample chunks at 44.1kHz)

## Common Tasks

### Adjusting Dance Responsiveness
The silence detection logic is at line ~2090. Key thresholds:
- `LOW_ENERGY_THRESHOLD = 10`: Energy below this triggers fast silence counter
- `silence_counter >= 22`: Stop threshold (~500ms)

### Adding New Dance Patterns
1. Create `DanceMove` objects in `_build_moves()` method
2. Create `DancePattern` in `_build_patterns()` method
3. Add pattern name to appropriate category in `pattern_categories`

### Adding New Eye Expressions
1. Define 8-byte bitmap in `EYE_PATTERNS` dictionary
2. Each byte represents one row (1 = LED on, 0 = LED off)

### Modifying Audio Detection
- `AudioClassifier.classify()`: Adjust scoring weights for different audio types
- Note: Both classifiers use 30-sample history (~700ms lag)

## Running the Project

```bash
# Without sudo (if user is in gpio group)
python3 robot_backend.py

# With sudo (for GPIO access)
sudo python3 robot_backend.py
```

Access at: `http://localhost:5000` or `http://<pi-ip>:5000`

## Testing

### Test Motor Control
```bash
curl -X POST http://localhost:5000/api/control/forward
sleep 0.5
curl -X POST http://localhost:5000/api/control/stop
```

### Test Autonomous Mode
```bash
curl -X POST http://localhost:5000/api/control/toggle_auto
curl -s http://localhost:5000/api/status | python3 -m json.tool
```

### Check Audio Status
```bash
curl -s http://localhost:5000/api/status | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'Energy: {d[\"energy\"]}%')
print(f'Audio Type: {d[\"audio_type\"]}')
print(f'Dancing: {d[\"is_dancing\"]}')
print(f'Autonomous: {d[\"autonomous\"]}')
"
```

## Important Notes

1. **GPIO Permissions**: User needs to be in `gpio` group or run with `sudo`
2. **Port Conflicts**: Kill existing processes on port 5000 before restarting
3. **Audio Device**: USB microphone should be the default input device
4. **Thread Safety**: Use `state_lock` when accessing/modifying `ui_state`
5. **LED Optional**: Code gracefully handles missing LED matrix hardware

## Debugging Tips

- Check backend output for errors (runs in terminal)
- Use browser dev tools to monitor `/api/status` responses
- Audio issues: Check `arecord -l` for available devices
- GPIO issues: Verify with `gpio readall` command

## File Purposes

| File | Purpose |
|------|---------|
| `robot_backend.py` | Main application - edit for logic changes |
| `robot_frontend.html` | UI - edit for interface changes |
| `*.old`, `*_old*.py` | Backup files - can be deleted |
| `*.wav` | Test audio files - not needed for operation |
