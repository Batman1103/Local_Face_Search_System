# FaceSearch Desktop

A local-first desktop face-search application for large personal photo collections.

## Final user workflow

1. Install FaceSearch once.
2. Open it from the desktop Applications menu.
3. Select a photo folder.
4. The first scan runs in the background.
5. A filesystem watcher automatically indexes new/changed images.
6. Deleted images are marked missing instead of immediately destroying the index.
7. If a drive/folder disappears, the saved collection remains and can be reconnected.
8. Search with a query photo; results show the original absolute path and can open the image or its containing folder.

Original photos never need to be copied into the FaceSearch data directory.

## Local data

The application stores its own index and metadata under the per-user application data directory:

Linux:
```text
~/.local/share/facesearch/
├── index/faces_hnsw.index
└── metadata.db
```

Windows and macOS use their normal per-user application-data locations.

## Linux development/install

Requirements: Python 3, Node.js/npm.

```bash
./scripts/install-linux.sh
./scripts/run-linux.sh
```

The installer creates the Python environment, installs backend dependencies, builds the React renderer, installs Electron, and creates a desktop entry.

To enable startup at login:

```bash
./scripts/enable-autostart.sh
```

The current autostart entry launches the desktop app in background/tray mode. The UI can be opened from the tray.

## Build a distributable Linux package

After dependencies are installed:

```bash
cd desktop
npm run dist
```

This builds an AppImage and `.deb`. The packaged application includes the backend source and built renderer; Python dependencies still need to be provisioned for the target machine. For a true single-file installer, bundle a matching Python runtime and the ML/ONNX wheels or provide them through an installer/bootstrap stage.

## Architecture

```text
Electron desktop UI
        │
        ▼
127.0.0.1:8765 FastAPI agent
        │
        ├── InsightFace / ONNX Runtime
        ├── FAISS HNSW vector index
        ├── SQLite metadata
        └── Watchdog folder watcher
        │
        ▼
User's existing photo folder
```

The index contains face embeddings and metadata, not copies of the original photos.

## Large collections

The indexer streams filesystem paths and processes files one at a time. It does not build a list of all images in RAM. For very large collections, CPU-only processing can still take a long time; a GPU-enabled inference worker is recommended for production-scale indexing.

The FAISS index uses inactive-vector tombstones when files are changed/deleted. A full rebuild endpoint is provided to reclaim tombstoned space; it intentionally reprocesses the connected folder.
