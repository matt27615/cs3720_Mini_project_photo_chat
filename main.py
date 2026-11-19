# Mini Project 1 - Chat about a Photo  |  FastAPI backend (deploy on Render as a Web Service)
# AI-assisted: first draft generated with Claude (Anthropic); reviewed, tested and adapted by the team.
# Based on class examples: Activity08/09 WebSocket chat (ConnectionManager) + file upload with StaticFiles.
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# ---------- 1) CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 2) Upload folder + StaticFiles ----------
# Render's disk is ephemeral: files in /tmp/uploads disappear on redeploy/restart (fine for this project).
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=UPLOAD_DIR), name="images")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


@app.get("/")
def root():
    return {"message": "Chat about a Photo backend is running"}


# ---------- 3) POST /upload-photo ----------
@app.post("/upload-photo")
async def upload_photo(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".jpg"  # camera photos are JPEG; default if the app sent a odd name

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    # unique name so two devices never overwrite each other's photo
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(contents)
    print(f"[upload] saved {path} ({len(contents)} bytes)")

    return {
        "filename": filename,
        "url": f"/images/{filename}",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------- 4) GET /photos (optional feature: browse older photos) ----------
@app.get("/photos")
def list_photos():
    photos = []
    for name in os.listdir(UPLOAD_DIR):
        full = os.path.join(UPLOAD_DIR, name)
        if not os.path.isfile(full):
            continue
        mtime = os.path.getmtime(full)
        photos.append(
            {
                "filename": name,
                "url": f"/images/{name}",
                "uploaded_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
            }
        )
    photos.sort(key=lambda p: p["uploaded_at"], reverse=True)  # newest first
    return photos


# ---------- 5) WebSocket /ws with broadcast ----------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # Remember the last shared photo so a device that joins later sees it too.
        self.current_photo_url: Optional[str] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[ws] client connected. Total: {len(self.active_connections)}")
        if self.current_photo_url:
            await websocket.send_text(
                json.dumps({"type": "photo_selected", "url": self.current_photo_url})
            )

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"[ws] client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, data: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(data)
            except Exception as e:
                print("[ws] error sending to a client:", e)
                self.disconnect(connection)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            print(f"[ws] received: {data[:200]}")
            try:
                msg = json.loads(data)
                if isinstance(msg, dict) and msg.get("type") == "photo_selected" and msg.get("url"):
                    manager.current_photo_url = msg["url"]
            except json.JSONDecodeError:
                pass  # plain text is still broadcast as-is
            await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print("[ws] unexpected error:", e)
        manager.disconnect(websocket)
