from pathlib import Path
from threading import Timer, RLock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from app.services.indexer import IMAGE_EXTS

class _Handler(FileSystemEventHandler):
    def __init__(self, indexer):
        self.indexer = indexer
        self.timers = {}
        self.lock = RLock()

    def _schedule(self, path):
        p = Path(path)
        if p.suffix.lower() not in IMAGE_EXTS:
            return
        with self.lock:
            old = self.timers.pop(str(p), None)
            if old:
                old.cancel()
            t = Timer(1.5, self._run, args=(p,))
            t.daemon = True
            self.timers[str(p)] = t
            t.start()

    def _run(self, p):
        with self.lock:
            self.timers.pop(str(p), None)
        self.indexer.enqueue(p)

    def on_created(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.indexer.db.mark_missing_path(event.src_path)
            self._schedule(event.dest_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.indexer.db.mark_missing_path(event.src_path)

class FolderWatcher:
    def __init__(self):
        self.observer = None

    def start(self, root, indexer):
        self.stop()
        self.observer = Observer()
        self.observer.schedule(_Handler(indexer), str(root), recursive=True)
        self.observer.start()

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None
