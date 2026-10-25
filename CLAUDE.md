# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

Robot Rave is a Raspberry Pi-based music-reactive dancing robot with LED matrix eyes. It listens to music via USB microphone, detects beats/BPM, and executes dance patterns synchronized to the music.

## Architecture

- **Backend**: `robot_backend.py` - Single-file Flask server (~2200 lines) containing all logic
- **Frontend**: `robot_frontend.html` - Web control interface served by Flask
- **3D Viewer**: `ravitto_splat.html` - WebGL Gaussian splat particle visualization
- **Photo Studio**: `ravitto_studio.html` - Interactive 360° photo viewer

```
USB Microphone → Audio Analysis → Beat Detection → Dance Engine → Motors (GPIO PWM)
                                                              → LED Eyes (I2C)
Web Browser ←→ Flask Server (port 5000)
            ├── /         → Control Panel
            ├── /splat    → 3D Gaussian Splat Viewer
            └── /studio   → 360° Photo Studio
```

## Key Code Locations in robot_backend.py

| Component | Line | Description |
|-----------|------|-------------|
| `EYE_PATTERNS` | ~114 | Eye bitmap definitions (8 bytes each) |
| `RobotEyes` | ~580 | LED matrix controller class |
| `drive()` | ~1024 | Motor control function |
| `AudioAnalyzer` | ~1090 | Audio feature extraction |
| `BeatDetector` | ~1230 | Beat/BPM detection |
| `MusicDetector` | ~1336 | Music vs silence classification |
| `AudioClassifier` | ~1387 | Audio type classification |
| `DanceMove/DancePattern` | ~1492 | Dance move definitions |
| `AdvancedDanceEngine` | ~1506 | Dance pattern selection |
| `motor_worker()` | ~1817 | Motor command queue processor |
| `audio_loop()` | ~1860 | Main processing loop (~43 Hz) |
| Silence detection | ~2090 | Quick-stop logic when music stops |

## Hardware

- **GPIO Pins**: Left motor (9, 10), Right motor (7, 8), Status LED (25)
- **I2C**: LED matrices at 0x70 (left eye), 0x71 (right eye)
- **Audio**: 44.1kHz sample rate, 2048 sample chunks

## Web control security

Set a random `ROBOT_API_TOKEN` before starting the backend (for example, generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`). Missing/invalid configuration stops before hardware initialization. Enter that token in the frontend; all API routes require its Bearer header. The default bind address is `127.0.0.1`; use SSH forwarding or a TLS proxy for remote access. `ROBOT_BIND_HOST` must be an explicit IP address for deliberate network binding. Plain LAN HTTP does not encrypt the token.

If using sudo, explicitly preserve only `ROBOT_API_TOKEN` and any configured `ROBOT_BIND_HOST` with `sudo --preserve-env=ROBOT_API_TOKEN,ROBOT_BIND_HOST python3 robot_backend.py`. Do not put secrets in command arguments or URLs.

Hardware-free regression checks: `python3 -m unittest discover -s tests -v` (Flask required), then `node --test tests/test_frontend.cjs`. These mock motors/eyes and execute only the web section; full Raspberry Pi behavior remains a separate operator check.

## Common Commands

```bash
# Run the robot
python3 robot_backend.py

# Test motor control
curl -H "Authorization: Bearer $ROBOT_API_TOKEN" -X POST http://localhost:5000/api/control/forward
curl -H "Authorization: Bearer $ROBOT_API_TOKEN" -X POST http://localhost:5000/api/control/stop

# Toggle autonomous mode
curl -H "Authorization: Bearer $ROBOT_API_TOKEN" -X POST http://localhost:5000/api/control/toggle_auto

# Check status
curl -H "Authorization: Bearer $ROBOT_API_TOKEN" -s http://localhost:5000/api/status | python3 -m json.tool
```

## API Endpoints

- `GET /` - Web frontend (control panel)
- `GET /splat` - 3D Gaussian splat viewer (WebGL particle visualization)
- `GET /studio` - Interactive 360° photo studio
- `GET /api/status` - Robot state JSON
- `POST /api/control/<cmd>` - Commands: forward, backward, left, right, stop, toggle_auto
- `POST /api/sens/<val>` - Set sensitivity (0-100)
- `POST /api/gain/<val>` - Set microphone gain (0-100)
- `POST /api/eyes/<expression>` - Set eye expression
- `POST /api/eyes/special/<type>` - Trigger special animation

## Gaussian Splat Viewer (ravitto_splat.html)

WebGL-based 3D particle visualization with:
- **Particle System**: 25k-200k particles forming robot shape
- **Shaders**: Custom vertex/fragment shaders for Gaussian blur effect
- **Controls**: Orbital camera (drag/scroll/touch), quality selector, color modes
- **Effects**: Explode animation, auto-rotate, floating particles
- **State**: Managed in global `state` object (rotation, zoom, particles, etc.)

Key functions:
- `generateRobotParticles()` - Creates particle positions/colors for robot shape
- `createViewMatrix()` / `createPerspectiveMatrix()` - Camera math
- `render()` - Main animation loop (~60 FPS)
- `updateBuffers()` - Updates WebGL buffers when particle count changes

## Development Notes

- **Thread safety**: Use `state_lock` when accessing/modifying `ui_state`
- **LED graceful degradation**: Code handles missing LED hardware automatically
- **GPIO permissions**: Run with `sudo` or ensure user is in `gpio` group
- **Port conflicts**: Kill existing processes on port 5000 before restarting
- **WebGL fallback**: Splat viewer checks for WebGL support and alerts if unavailable
