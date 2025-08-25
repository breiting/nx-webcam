# 🖥️ nx-webcam

A lightweight Python application that turns any **USB UVC webcam** (e.g. ELP) into:

- 📸 A **snapshot** and **MJPEG livestream** server powered by FastAPI
- 🔗 A **Prusa Connect Camera API client** – automatically pushing snapshots to your printer in Prusa Connect
- 🐳 A fully **containerized service** (with [uv](https://github.com/astral-sh/uv) as the Python package manager)
- ⚡ Ready to run behind **Traefik** or any reverse proxy for intranet access

Perfect for monitoring your Prusa printer.

## ✨ Features

- **FastAPI + Uvicorn** web server
- **MJPEG endpoint** (`/mjpeg`) for live view
- **Snapshot endpoint** (`/snapshot.jpg`)
- **Health endpoint** (`/health`)
- **Prusa Connect Camera API integration**
  - Uploads snapshots periodically via `PUT /c/snapshot` with `Token` + `Fingerprint`
  - Compatible with “Other Camera” setup in Prusa Connect
- **Dockerized** for reproducible deployment
- **Traefik-ready** with proper labels & streaming timeouts

## 🚀 Quickstart

### 1. Clone & configure

```bash
git clone https://github.com/breiting/nx-webcam.git
cd nx-webcam
cp .env.example .env
```

Edit .env:

```ini
# Camera device

CAM_DEVICE=/dev/video0
CAM_WIDTH=1280
CAM_HEIGHT=720
CAM_FPS=15

# Prusa Connect (leave empty if you only want local web UI)

PRUSA_BASE_URL=https://webcam.connect.prusa3d.com
PRUSA_TOKEN=your-token-here
PRUSA_FINGERPRINT=printercam-01
PUSH_EVERY=10
```

### 2. Run with Docker Compose

```
docker compose up -d --build
```

### 3. Access

- Web UI: http://nx-webcam.lan/ (through Traefik)
- Snapshot: http://nv-webcam.lan/snapshot.jpg
- MJPEG stream: http://nv-webcam.lan/mjpeg

🔗 Prusa Connect Setup 1. In Prusa Connect, go to your printer → Camera → Add other camera 2. Copy the token provided and set it in your .env 3. Pick a Fingerprint (any unique identifier, keep it stable) 4. The app pushes snapshots every PUSH_EVERY seconds (default: 10s) 5. Images appear in Connect when your printer is online

## 🛠️ Development

Run locally with uv:

```
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 📷 Camera Notes

- If your webcam supports MJPEG, prefer it (lower CPU, no decoding needed).
- If it only outputs H.264, set GST_PIPE in .env, e.g.:

`GST_PIPE=v4l2src device=/dev/video0 ! video/x-h264,framerate=15/1 ! h264parse ! avdec_h264 ! videoconvert ! appsink`

Check available formats:

```
v4l2-ctl -d /dev/video0 --list-formats-ext
```

## ⚖️ License

MIT License © 2025 Bernhard Reitinger

## 🙌 Credits

- Built with FastAPI + OpenCV
- Uses uv for dependency management
- Integrates with Prusa Connect Camera API ([https://connect.prusa3d.com/docs/cameras/openapi/])
