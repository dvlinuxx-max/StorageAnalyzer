import os
import hashlib
import time


def _hash_file(path, partial=False, chunk=1024 * 1024):
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb", buffering=0) as f:
            if partial:
                data = f.read(64 * 1024)
                h.update(data)
            else:
                while True:
                    block = f.read(chunk)
                    if not block:
                        break
                    h.update(block)
        return h.hexdigest()
    except (PermissionError, OSError):
        return None


class DuplicateEngine:

    def __init__(self, root_path, min_size, progress_q, stop_event):
        self.root = root_path
        self.min_size = min_size
        self.q = progress_q
        self.stop = stop_event

    def run(self):
        try:
            self._find()
        except Exception as e:
            self.q.put(("dup_error", str(e)))

    def _find(self):
        by_size = {}
        scanned = 0
        last = time.time()
        for dirpath, dirnames, filenames in os.walk(self.root):
            if self.stop.is_set():
                self.q.put(("dup_cancelled", None)); return
            low = dirpath.lower()
            if "\\windows\\winsxs" in low or "\\$recycle.bin" in low:
                dirnames[:] = []
                continue
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                if sz < self.min_size:
                    continue
                by_size.setdefault(sz, []).append(fp)
                scanned += 1
                if time.time() - last > 0.2:
                    last = time.time()
                    self.q.put(("dup_progress", f"المسح: {scanned:,} ملف مرشح | {dirpath[:60]}"))

        candidates = {s: ps for s, ps in by_size.items() if len(ps) > 1}

        groups = {}
        total_groups = len(candidates)
        done = 0
        for sz, paths in candidates.items():
            if self.stop.is_set():
                self.q.put(("dup_cancelled", None)); return
            done += 1
            partial = {}
            for p in paths:
                ph = _hash_file(p, partial=True)
                if ph:
                    partial.setdefault((sz, ph), []).append(p)
            for key, plist in partial.items():
                if len(plist) < 2:
                    continue
                for p in plist:
                    fh = _hash_file(p, partial=False)
                    if fh:
                        groups.setdefault((sz, fh), []).append(p)
            if done % 5 == 0:
                self.q.put(("dup_progress",
                            f"تحليل البصمات: {done}/{total_groups} مجموعة حجم"))

        result = []
        wasted = 0
        for (sz, fh), plist in groups.items():
            if len(plist) > 1:
                result.append({"size": sz, "count": len(plist), "paths": sorted(plist)})
                wasted += sz * (len(plist) - 1)
        result.sort(key=lambda g: g["size"] * (g["count"] - 1), reverse=True)
        self.q.put(("dup_done", {"groups": result, "wasted": wasted}))
