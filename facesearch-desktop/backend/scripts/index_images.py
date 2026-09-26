import argparse, sys
from pathlib import Path
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_config
from app.db.database import MetadataDB
from app.services.face_engine import FaceEngine
from app.services.vector_store import VectorStore

EXTENSIONS = {".jpg",".jpeg",".png",".webp"}

def iter_images(root):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTENSIONS:
            yield p

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    args = parser.parse_args()

    cfg = load_config()
    image_root = Path(args.root or cfg["images_root"]).expanduser().resolve()
    if not image_root.is_dir():
        raise SystemExit(f"Image directory does not exist: {image_root}")

    db = MetadataDB(cfg["database_path"])
    engine = FaceEngine(cfg["model"]["name"], cfg["model"]["providers"])
    store = VectorStore(cfg["index_dir"])

    images = list(iter_images(image_root))
    print(f"Found {len(images):,} images")
    print(f"Existing vectors: {store.index.ntotal:,}")

    added = skipped = failed = faces_added = 0

    for path in tqdm(images, unit="img"):
        try:
            st = path.stat()
            image_id, changed = db.upsert_image(str(path), st.st_size, st.st_mtime_ns)
            if not changed:
                skipped += 1
                continue

            faces = engine.detect(engine.read_image(path))
            if faces:
                start = store.index.ntotal
                store.add([f["embedding"] for f in faces])
                for i, f in enumerate(faces):
                    db.add_face(start+i, image_id, f["bbox"], f["score"])
                faces_added += len(faces)
            db.commit()
            added += 1

            if added % 100 == 0:
                store.save()

        except Exception as e:
            failed += 1
            print(f"\n[ERROR] {path}: {e}")

    store.save()
    print("\nDone")
    print(f"Changed/new images: {added:,}")
    print(f"Skipped unchanged images: {skipped:,}")
    print(f"Failures: {failed:,}")
    print(f"Faces added this run: {faces_added:,}")
    print(f"Total vectors: {store.index.ntotal:,}")

if __name__ == "__main__":
    main()
