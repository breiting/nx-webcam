import os
import time
import threading
import cv2
import requests
from fastapi import FastAPI, Response
from starlette.responses import StreamingResponse

# --- Konfiguration per ENV ---
DEVICE = os.getenv("CAM_DEVICE", "/dev/video0")
WIDTH = int(os.getenv("CAM_WIDTH", "1280"))
HEIGHT = int(os.getenv("CAM_HEIGHT", "720"))
FPS = int(os.getenv("CAM_FPS", "15"))

GST_PIPE = os.getenv(
    "GST_PIPE",
    f"v4l2src device={DEVICE} ! image/jpeg,framerate={FPS}/1 ! jpegdec ! videoconvert ! appsink",
)

PRUSA_URL = os.getenv("PRUSA_URL", "")
PRUSA_TOKEN = os.getenv("PRUSA_TOKEN", "")
PUSH_EVERY = float(os.getenv("PUSH_EVERY", "5"))

app = FastAPI()
_cap_lock = threading.Lock()
_last_jpeg: bytes | None = None
_stop = False


def open_capture() -> cv2.VideoCapture:
    """Versuche zuerst GStreamer, dann direktes V4L2/MJPEG."""
    cap = cv2.VideoCapture(GST_PIPE, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        cap = cv2.VideoCapture(DEVICE)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, FPS)
    if not cap.isOpened():
        raise RuntimeError("Could not open camera.")
    return cap


def grabber():
    global _last_jpeg
    cap = open_capture()
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
    while not _stop:
        ok, frame = cap.read()
        if not ok:
            cap.release()
            time.sleep(0.5)
            try:
                cap = open_capture()
            except Exception:
                time.sleep(1.0)
            continue

        if WIDTH and HEIGHT and (frame.shape[1] != WIDTH or frame.shape[0] != HEIGHT):
            frame = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", frame, encode_param)
        if ok:
            data = jpg.tobytes()
            with _cap_lock:
                _last_jpeg = data

    cap.release()


def prusa_pusher():
    if not PRUSA_URL or not PRUSA_TOKEN:
        return
    sess = requests.Session()
    headers = {"Authorization": f"Bearer {PRUSA_TOKEN}"}
    while not _stop:
        time.sleep(PUSH_EVERY)
        with _cap_lock:
            data = _last_jpeg
        if not data:
            continue
        try:
            resp = sess.post(
                PRUSA_URL,
                headers=headers,
                files={"image": ("snapshot.jpg", data, "image/jpeg")},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[prusa] upload failed: {e}")


@app.on_event("startup")
def _startup():
    threading.Thread(target=grabber, daemon=True).start()
    threading.Thread(target=prusa_pusher, daemon=True).start()


@app.get("/snapshot.jpg")
def snapshot():
    with _cap_lock:
        data = _last_jpeg
    if not data:
        return Response(status_code=503)
    return Response(content=data, media_type="image/jpeg")


@app.get("/mjpeg")
def mjpeg():
    def gen():
        boundary = "frame"
        while True:
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
            time.sleep(1.0 / max(FPS, 5))

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/")
def index():
    html = """
    <html>
      <head><title>Printer Cam</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
      <body style="margin:0;background:#111;color:#eee;font-family:sans-serif;">
        <div style="max-width:900px;margin:1rem auto;padding:0 1rem;">
          <h2>Live</h2>
          <img src="/mjpeg" style="width:100%;height:auto;display:block;border-radius:8px" />
          <p><a href="/snapshot.jpg" style="color:#8cf">Snapshot</a></p>
        </div>
      </body>
    </html>
    """
    return Response(content=html, media_type="text/html")
