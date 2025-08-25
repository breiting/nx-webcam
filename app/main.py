import os
import time
import threading
import logging
from contextlib import asynccontextmanager
from typing import Optional

import cv2
import requests
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

DEVICE = os.getenv("CAM_DEVICE", "/dev/video0")
WIDTH = int(os.getenv("CAM_WIDTH", "1280"))
HEIGHT = int(os.getenv("CAM_HEIGHT", "720"))
FPS = int(os.getenv("CAM_FPS", "15"))

GST_PIPE = os.getenv(
    "GST_PIPE",
    f"v4l2src device={DEVICE} ! image/jpeg,framerate={FPS}/1 ! jpegdec ! videoconvert ! appsink",
)

JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))

# Prusa Connect
PRUSA_BASE_URL = os.getenv("PRUSA_BASE_URL", "https://webcam.connect.prusa3d.com")
PRUSA_TOKEN = os.getenv("PRUSA_TOKEN", "")
PRUSA_FINGERPRINT = os.getenv("PRUSA_FINGERPRINT", "")
PUSH_EVERY = float(os.getenv("PUSH_EVERY", "10"))  # Sekunden

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("printer-cam")

# -------------------------
# App State
# -------------------------
_last_jpeg: Optional[bytes] = None
_cap_lock = threading.Lock()
stop_event = threading.Event()
threads: list[threading.Thread] = []


# -------------------------
# Capture Helpers
# -------------------------
def _has_gstreamer_support() -> bool:
    return hasattr(cv2, "CAP_GSTREAMER")


def _open_with_gstreamer() -> Optional[cv2.VideoCapture]:
    if not _has_gstreamer_support():
        return None
    cap = cv2.VideoCapture(GST_PIPE, cv2.CAP_GSTREAMER)
    if cap is not None and cap.isOpened():
        log.info("Capture geöffnet via GStreamer pipeline.")
        return cap
    return None


def _open_with_v4l2() -> Optional[cv2.VideoCapture]:
    cap = cv2.VideoCapture(DEVICE)
    if cap is None or not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except Exception as e:
        log.debug(f"Konnte FOURCC nicht setzen: {e}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, FPS)

    if cap.isOpened():
        log.info("Capture geöffnet via direktes V4L2.")
        return cap
    return None


def open_capture() -> cv2.VideoCapture:
    cap = _open_with_gstreamer()
    if cap:
        return cap
    cap = _open_with_v4l2()
    if cap:
        return cap
    raise RuntimeError(
        "Kamera konnte nicht geöffnet werden (GStreamer und V4L2 fehlgeschlagen)."
    )


# -------------------------
# Worker Threads
# -------------------------
def grabber_worker():
    global _last_jpeg
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

    while not stop_event.is_set():
        try:
            cap = open_capture()
        except Exception as e:
            log.error(f"Capture open failed: {e}")
            time.sleep(1.0)
            continue

        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                log.warning("Frame read failed – versuche Reconnect …")
                cap.release()
                time.sleep(0.5)
                break

            try:
                if (
                    WIDTH
                    and HEIGHT
                    and (frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT)
                ):
                    frame = cv2.resize(
                        frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA
                    )
            except Exception as e:
                log.debug(f"Resize skipped: {e}")

            ok, buf = cv2.imencode(".jpg", frame, encode_param)
            if ok:
                data = buf.tobytes()
                with _cap_lock:
                    _last_jpeg = data

            time.sleep(max(0.0, 1.0 / max(FPS, 5)) / 2.0)

        time.sleep(0.2)


def prusa_pusher_worker():
    if not PRUSA_TOKEN or not PRUSA_FINGERPRINT:
        log.info(
            "PRUSA_TOKEN oder PRUSA_FINGERPRINT nicht gesetzt – Prusa-Upload ist deaktiviert."
        )
        return

    url = f"{PRUSA_BASE_URL.rstrip('/')}/c/snapshot"
    headers = {
        "Token": PRUSA_TOKEN,
        "Fingerprint": PRUSA_FINGERPRINT,
    }
    sess = requests.Session()

    log.info("Prusa-Pusher aktiv.")
    while not stop_event.is_set():
        time.sleep(PUSH_EVERY)
        with _cap_lock:
            data = _last_jpeg
        if not data:
            continue
        try:
            resp = sess.put(
                url,
                headers=headers,
                files={"image": ("snapshot.jpg", data, "image/jpeg")},
                timeout=10,
            )
            if resp.status_code >= 400:
                log.warning(f"[prusa] HTTP {resp.status_code} – {resp.text[:200]}")
            else:
                log.debug("[prusa] Snapshot uploaded.")
        except Exception as e:
            log.warning(f"[prusa] upload failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start
    stop_event.clear()
    t1 = threading.Thread(target=grabber_worker, name="grabber", daemon=True)
    t1.start()
    threads.append(t1)

    t2 = threading.Thread(target=prusa_pusher_worker, name="prusa", daemon=True)
    t2.start()
    threads.append(t2)

    log.info("Service gestartet. Endpoints: /  /snapshot.jpg  /mjpeg  /health")
    try:
        yield
    finally:
        # Stop
        stop_event.set()
        # Wir geben Threads Zeit zum sauberen Ausstieg
        for t in threads:
            if t.is_alive():
                t.join(timeout=2.0)
        log.info("Service gestoppt.")


app = FastAPI(lifespan=lifespan)

# -------------------------
# Middleware & Routes
# -------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ggf. einschränken
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)


@app.options("/{full_path:path}")
def any_options(full_path: str):
    # Preflight-Response ohne Content
    return Response(status_code=204)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/snapshot.jpg")
def snapshot():
    with _cap_lock:
        data = _last_jpeg
    if not data:
        # Noch kein Frame verfügbar
        return Response(status_code=503)
    return Response(content=data, media_type="image/jpeg")


@app.get("/mjpeg")
def mjpeg():
    boundary = "frame"

    def gen():
        while True:
            if stop_event.is_set():
                break
            with _cap_lock:
                data = _last_jpeg
            if data:
                yield (
                    b"--" + boundary.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(data)).encode()
                    + b"\r\n\r\n"
                    + data
                    + b"\r\n"
                )
            # Intervall leicht kürzer als FPS, aber nicht zu hoch
            time.sleep(max(0.02, 1.0 / max(FPS, 5)))

    return StreamingResponse(
        gen(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
def index():
    html = """
    <html>
      <head>
        <title>Printer Cam</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body{margin:0;background:#111;color:#eee;font-family:system-ui,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,sans-serif}
          .wrap{max-width:900px;margin:1rem auto;padding:0 1rem}
          img{width:100%;height:auto;display:block;border-radius:8px;background:#000}
          a{color:#8cf}
          .grid{display:grid;gap:0.75rem;grid-template-columns:1fr;align-items:center}
          @media(min-width:800px){.grid{grid-template-columns:1fr auto}}
          .card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:0.75rem 1rem}
          .muted{color:#bbb;font-size:0.9rem}
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="grid">
            <h2 style="margin:0">Live</h2>
            <div class="card">
              <span class="muted">Endpoints:</span>
              <code>/mjpeg</code> · <code>/snapshot.jpg</code> · <code>/health</code>
            </div>
          </div>
          <img src="/mjpeg" />
          <p><a href="/snapshot.jpg">Snapshot</a></p>
        </div>
      </body>
    </html>
    """
    return Response(content=html, media_type="text/html")
