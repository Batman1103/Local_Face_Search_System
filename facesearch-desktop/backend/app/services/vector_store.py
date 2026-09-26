from pathlib import Path
import faiss
import numpy as np

DIM = 512

class VectorStore:
    def __init__(self, index_dir):
        self.dir = Path(index_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "faces_hnsw.index"
        self.load()

    @staticmethod
    def new_index():
        idx = faiss.IndexHNSWFlat(DIM, 32, faiss.METRIC_INNER_PRODUCT)
        idx.hnsw.efConstruction = 80
        idx.hnsw.efSearch = 64
        return idx

    def load(self):
        if self.path.exists():
            self.index = faiss.read_index(str(self.path))
            self.index.hnsw.efSearch = 64
        else:
            self.index = self.new_index()

    def save(self):
        tmp = self.path.with_suffix('.index.tmp')
        faiss.write_index(self.index, str(tmp))
        tmp.replace(self.path)

    def add(self, embeddings):
        arr = np.asarray(embeddings, dtype="float32")
        if len(arr) == 0:
            return
        faiss.normalize_L2(arr)
        self.index.add(arr)

    def search(self, embedding, top_k):
        q = np.asarray([embedding], dtype="float32")
        faiss.normalize_L2(q)
        return self.index.search(q, top_k)

    def rebuild(self, embeddings):
        idx = self.new_index()
        if embeddings:
            arr = np.asarray(embeddings, dtype='float32')
            faiss.normalize_L2(arr)
            idx.add(arr)
        self.index = idx
        self.save()
