from pathlib import Path
import os
import platform
import subprocess
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import load_config
from app.db.database import MetadataDB
from app.services.face_engine import FaceEngine
from app.services.vector_store import VectorStore
from app.services.indexer import IncrementalIndexer
from app.services.watcher import FolderWatcher

cfg = load_config()
app = FastAPI(title="FaceSearch Desktop Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = MetadataDB(cfg["database_path"])
engine = FaceEngine(cfg["model"]["name"], cfg["model"]["providers"])
store = VectorStore(cfg["index_dir"])
watcher = FolderWatcher()
indexer = None
indexer_lock = threading.RLock()

class FolderRequest(BaseModel):
    path: str


def create_indexer(root: str):
    global indexer
    with indexer_lock:
        if indexer:
            watcher.stop()
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise ValueError(f"Folder does not exist: {root_path}")
        old_root = db.get_state("root")
        if old_root and Path(old_root).is_dir() and Path(old_root).resolve() != root_path:
            db.rebind_root(old_root, root_path)
        indexer = IncrementalIndexer(root_path, engine, store, db)
        db.set_state("root", str(root_path))
        watcher.start(root_path, indexer)
        indexer.start_scan()

saved_root = db.get_state("root")
if saved_root and Path(saved_root).is_dir():
    try:
        create_indexer(saved_root)
    except Exception:
        pass

@app.get("/api/health")
def health():
    return {"status": "ok", **db.stats(), "vectors": store.index.ntotal}

@app.get("/api/stats")
def stats():
    result = {**db.stats(), "vectors": store.index.ntotal}
    result["root"] = db.get_state("root")
    saved_root = result["root"]
    result["folder_exists"] = bool(saved_root and Path(saved_root).is_dir())
    result["index_dir"] = str(Path(cfg["index_dir"]).resolve())
    result["database"] = str(Path(cfg["database_path"]).resolve())
    result["index_status"] = indexer.status() if indexer else {"running": False, "queued": 0}
    return result

@app.post("/api/folder/connect")
def connect_folder(req: FolderRequest):
    try:
        create_indexer(req.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "root": str(Path(req.path).expanduser().resolve())}

@app.post("/api/folder/disconnect")
def disconnect_folder():
    global indexer
    with indexer_lock:
        watcher.stop()
        indexer = None
        db.set_state("root", "")
    return {"ok": True}

@app.post("/api/folder/rescan")
def rescan_folder():
    if not indexer:
        raise HTTPException(409, "No photo folder connected")
    indexer.start_scan()
    return {"ok": True}

@app.post("/api/index/rebuild")
def rebuild_index():
    """Rebuild FAISS from currently active face metadata to reclaim tombstoned vectors."""
    global indexer
    if not indexer:
        raise HTTPException(409, "No photo folder connected")
    # Existing FAISS vectors cannot be compacted in-place safely. Reset the
    # vector index and mark every image stale so the next scan regenerates
    # embeddings and creates a clean index.
    db.reset_for_rebuild()
    store.index = store.new_index()
    store.save()
    db.set_state("rebuild_requested", datetime.now(timezone.utc).isoformat())
    indexer.start_scan()
    return {"ok": True, "message": "Vector index reset. A full folder scan has been queued."}

@app.post("/api/search")
async def search(
    file: UploadFile = File(...),
    top_k: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.45, ge=-1.0, le=1.0)
):
    if store.index.ntotal == 0:
        raise HTTPException(409, "Vector index is empty. Connect a photo folder and wait for indexing.")
    data = await file.read()
    try:
        faces = engine.detect(engine.read_image(data))
    except Exception as e:
        raise HTTPException(400, f"Invalid image: {e}")
    if not faces:
        raise HTTPException(400, "No face detected in query image")

    results = []
    for qno, face in enumerate(faces):
        search_k = min(max(top_k * 5, 50), store.index.ntotal)
        scores, ids = store.search(face["embedding"], search_k)
        for score, vid in zip(scores[0], ids[0]):
            vid = int(vid)
            if vid < 0 or float(score) < threshold:
                continue
            row = db.get_face(vid, active_only=True)
            if not row:
                continue
            _, image_id, image_path, x1, y1, x2, y2, det_score = row
            results.append({
                "query_face": qno,
                "vector_id": vid,
                "image_id": int(image_id),
                "image_path": image_path,
                "similarity": float(score),
                "face_bbox": [x1, y1, x2, y2],
                "detection_confidence": float(det_score),
            })

    results.sort(key=lambda r: r["similarity"], reverse=True)
    unique, seen = [], set()
    for r in results:
        key = (r["query_face"], r["image_id"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return {"query_faces": len(faces), "results": unique[:top_k]}

@app.get("/api/image/{vector_id}")
def image(vector_id: int):
    row = db.get_face(vector_id, active_only=True)
    if not row:
        raise HTTPException(404, "Image not found in active index")
    path = Path(row[2]).resolve()
    if not path.is_file():
        raise HTTPException(404, "Original image no longer exists")
    return FileResponse(path)

@app.post("/api/open-image/{vector_id}")
def open_image(vector_id: int):
    row = db.get_face(vector_id, active_only=True)
    if not row:
        raise HTTPException(404, "Image not found")
    path = Path(row[2]).resolve()
    if not path.is_file():
        raise HTTPException(404, "Original image no longer exists")
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        raise HTTPException(500, f"Could not open image: {e}")
    return {"ok": True, "path": str(path)}

@app.post("/api/open-folder/{vector_id}")
def open_folder(vector_id: int):
    row = db.get_face(vector_id, active_only=True)
    if not row:
        raise HTTPException(404, "Image not found")
    path = Path(row[2]).resolve()
    if not path.is_file():
        raise HTTPException(404, "Original image no longer exists")
    try:
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as e:
        raise HTTPException(500, f"Could not open folder: {e}")
    return {"ok": True, "path": str(path.parent)}
