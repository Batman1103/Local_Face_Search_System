from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Event, RLock
import os

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}

class IncrementalIndexer:
    def __init__(self, root, engine, store, db):
        self.root = Path(root).expanduser().resolve()
        self.engine = engine
        self.store = store
        self.db = db
        self.queue = Queue()
        self.stop_event = Event()
        self.lock = RLock()
        self.processed = 0
        self.discovered = 0
        self.errors = 0
        self.last_error = ''
        self.scan_running = False
        self.worker = Thread(target=self._worker, daemon=True)
        self.worker.start()

    def is_image(self, path):
        return Path(path).suffix.lower() in IMAGE_EXTS

    def enqueue(self, path):
        path = Path(path)
        if not path.is_file() or not self.is_image(path):
            return
        self.queue.put(path.resolve())
        self.discovered += 1

    def scan(self):
        self.scan_running = True
        try:
            for root, _, files in os.walk(self.root):
                for name in files:
                    p = Path(root) / name
                    if self.is_image(p):
                        self.enqueue(p)
            self.db.mark_missing_under_root(self.root)
        finally:
            self.scan_running = False

    def start_scan(self):
        if self.scan_running:
            return
        Thread(target=self.scan, daemon=True).start()

    def process(self, path):
        try:
            st = path.stat()
            relative = str(path.relative_to(self.root))
        except (FileNotFoundError, ValueError):
            return
        image_id, needs = self.db.prepare_image(path, st.st_size, st.st_mtime_ns, relative)
        if not needs:
            return
        try:
            data = path.read_bytes()
            faces = self.engine.detect(self.engine.read_image(data))
            embeddings = [f['embedding'] for f in faces]
            if embeddings:
                with self.lock:
                    start_id = self.store.index.ntotal
                    self.store.add(embeddings)
                    rows = []
                    for offset, face in enumerate(faces):
                        rows.append((start_id + offset, image_id, *map(int, face['bbox']), float(face['score'])))
                    self.db.add_faces(rows)
                    self.store.save()
            self.db.finish_image(image_id, True)
            self.processed += 1
        except Exception as exc:
            self.last_error = f'{path}: {exc}'
            self.errors += 1
            self.db.finish_image(image_id, False)

    def _worker(self):
        while not self.stop_event.is_set():
            try:
                path = self.queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                self.process(path)
            finally:
                self.queue.task_done()

    def status(self):
        return {
            'root': str(self.root),
            'queued': self.queue.qsize(),
            'discovered': self.discovered,
            'processed_this_run': self.processed,
            'errors_this_run': self.errors,
            'last_error': self.last_error,
            'running': self.scan_running or self.queue.qsize() > 0,
        }
