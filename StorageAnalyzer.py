import os
import sys
import threading
import queue
import time
import ctypes
import subprocess
import string
import shutil

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import fileinfo
from duplicates import DuplicateEngine

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False


def human_size(num):
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num) < 1024.0:
            return f"{num:,.1f} {unit}"
        num /= 1024.0
    return f"{num:,.1f} EB"


def open_in_explorer(path):
    try:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.run(["explorer", "/select,", os.path.normpath(path)])
    except Exception as e:
        messagebox.showerror("خطأ", f"تعذر فتح المسار:\n{e}")


PROTECTED = {
    os.path.normcase(os.path.expandvars(p))
    for p in (
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
        r"C:\ProgramData", r"%SystemRoot%", r"C:\Users",
    )
}


def is_protected(path):
    np = os.path.normcase(os.path.normpath(path))
    if np in PROTECTED:
        return True
    if len(np) <= 3 and np[1:].startswith(":"):
        return True
    return False


class ScanEngine:
    def __init__(self, root_path, progress_q, stop_event):
        self.root_path = root_path
        self.q = progress_q
        self.stop = stop_event
        self.total_bytes = 0
        self.file_count = 0
        self.dir_sizes = {}
        self.big_files = []
        self.cat_bytes = {}

    def run(self):
        try:
            self._scan(self.root_path)
        except Exception as e:
            self.q.put(("error", str(e)))
            return
        if self.stop.is_set():
            self.q.put(("cancelled", None))
            return
        self.big_files.sort(key=lambda x: x[1], reverse=True)
        self.q.put(("done", {
            "dir_sizes": self.dir_sizes,
            "big_files": self.big_files[:1000],
            "total_bytes": self.total_bytes,
            "file_count": self.file_count,
            "cat_bytes": self.cat_bytes,
        }))

    def _quick_cat(self, name):
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        d = fileinfo._EXT_MAP.get(ext)
        return d[1] if d else "other"

    def _scan(self, path):
        stack = [path]
        direct = {}
        children = {}
        last_report = time.time()
        all_dirs = []
        while stack:
            if self.stop.is_set():
                return
            cur = stack.pop()
            all_dirs.append(cur)
            direct.setdefault(cur, 0)
            children.setdefault(cur, [])
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        if self.stop.is_set():
                            return
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                children[cur].append(entry.path)
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                sz = entry.stat(follow_symlinks=False).st_size
                                direct[cur] += sz
                                self.total_bytes += sz
                                self.file_count += 1
                                cat = self._quick_cat(entry.name)
                                self.cat_bytes[cat] = self.cat_bytes.get(cat, 0) + sz
                                if sz >= 5 * 1024 * 1024:
                                    self.big_files.append((entry.path, sz))
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
            now = time.time()
            if now - last_report > 0.15:
                last_report = now
                self.q.put(("progress", {
                    "current": cur, "total_bytes": self.total_bytes,
                    "file_count": self.file_count,
                }))
        if self.stop.is_set():
            return
        sizes = dict(direct)
        for d in sorted(all_dirs, key=lambda p: p.count(os.sep), reverse=True):
            total = direct.get(d, 0)
            for ch in children.get(d, []):
                total += sizes.get(ch, 0)
            sizes[d] = total
        self.dir_sizes = sizes


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("محلل مساحة التخزين")
        self.geometry("1280x760")
        self.minsize(1040, 620)

        self.stop_event = threading.Event()
        self.progress_q = queue.Queue()
        self.dup_stop = threading.Event()
        self.dup_q = queue.Queue()
        self.result = None
        self.current_root = None
        self.dup_groups = []
        self._info_cache = {}

        self._build_style()
        self._build_widgets()
        self._refresh_drives()

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=6)

    def _build_widgets(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="اختر القرص:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.drive_var = tk.StringVar()
        self.drive_combo = ttk.Combobox(top, textvariable=self.drive_var, width=12, state="readonly")
        self.drive_combo.pack(side="left", padx=(6, 4))
        ttk.Button(top, text="مجلد محدد", command=self._pick_folder).pack(side="left", padx=4)
        self.scan_btn = ttk.Button(top, text="ابدأ الفحص", style="Big.TButton", command=self._start_scan)
        self.scan_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(top, text="ايقاف", command=self._stop_scan, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(top, text="تحديث الاقراص", command=self._refresh_drives).pack(side="right")

        self.drives_frame = ttk.LabelFrame(self, text="حالة الاقراص", padding=8)
        self.drives_frame.pack(fill="x", padx=10, pady=(0, 6))

        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=10, pady=4)

        self.nb = ttk.Notebook(main)
        self.nb.pack(side="left", fill="both", expand=True)
        self._build_tree_tab()
        self._build_files_tab()
        self._build_dup_tab()
        self._build_junk_tab()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._clear_details())

        self._build_details_panel(main)

        bottom = ttk.Frame(self, padding=(10, 6))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=200)
        self.progress.pack(side="left")
        self.status = ttk.Label(bottom, text="جاهز", font=("Segoe UI", 9))
        self.status.pack(side="left", padx=12)
        mode = "سلة المهملات" if HAS_SEND2TRASH else "حذف نهائي"
        self.del_btn = ttk.Button(bottom, text=f"حذف المحدد ({mode})", style="Danger.TButton",
                                  command=self._delete_selected)
        self.del_btn.pack(side="right")
        self.open_btn = ttk.Button(bottom, text="فتح الموقع", command=self._open_selected)
        self.open_btn.pack(side="right", padx=6)

    def _build_tree_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  شجرة المجلدات  ")
        self.tree = ttk.Treeview(frame, columns=("size", "pct"), selectmode="extended")
        self.tree.heading("#0", text="المجلد", anchor="w")
        self.tree.heading("size", text="الحجم", anchor="e")
        self.tree.heading("pct", text="النسبة", anchor="w")
        self.tree.column("#0", width=460, anchor="w")
        self.tree.column("size", width=120, anchor="e")
        self.tree.column("pct", width=170, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_expand)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_details_for(self._tree_sel_path()))
        self.tree.bind("<Double-1>", lambda e: self._open_selected())

    def _build_files_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  اكبر الملفات  ")
        bar = ttk.Frame(frame, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="تصفية:").pack(side="left")
        self.filter_var = tk.StringVar(value="الكل")
        fcombo = ttk.Combobox(bar, textvariable=self.filter_var, width=22, state="readonly",
                              values=["الكل", "وسائط", "مستندات", "مضغوط",
                                      "تنفيذي", "نظام او برنامج", "مخلفات", "غير مصنف"])
        fcombo.pack(side="left", padx=6)
        fcombo.bind("<<ComboboxSelected>>", lambda e: self._populate_files())

        cont = ttk.Frame(frame)
        cont.pack(fill="both", expand=True)
        self.files_tree = ttk.Treeview(cont, columns=("size", "cat", "owner", "path"),
                                       show="headings", selectmode="extended")
        for col, txt, w, anc in (("size", "الحجم", 100, "e"), ("cat", "النوع", 150, "w"),
                                 ("owner", "تابع لـ", 220, "w"), ("path", "المسار", 480, "w")):
            self.files_tree.heading(col, text=txt, anchor=anc)
            self.files_tree.column(col, width=w, anchor=anc)
        vsb = ttk.Scrollbar(cont, orient="vertical", command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=vsb.set)
        self.files_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.files_tree.bind("<<TreeviewSelect>>", lambda e: self._show_details_for(self._files_sel_path()))
        self.files_tree.bind("<Double-1>", lambda e: self._open_selected())

    def _build_dup_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  الملفات المكررة  ")
        bar = ttk.Frame(frame, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="ابحث عن المكررات", command=self._start_dup_scan).pack(side="left")
        ttk.Label(bar, text="  الحد الادنى للحجم:").pack(side="left")
        self.dup_min_var = tk.StringVar(value="1 MB")
        ttk.Combobox(bar, textvariable=self.dup_min_var, width=10, state="readonly",
                     values=["100 KB", "500 KB", "1 MB", "5 MB", "10 MB", "50 MB"]).pack(side="left", padx=4)
        self.dup_stop_btn = ttk.Button(bar, text="ايقاف", command=lambda: self.dup_stop.set(), state="disabled")
        self.dup_stop_btn.pack(side="left", padx=6)
        self.dup_summary = ttk.Label(bar, text="", font=("Segoe UI", 9, "bold"))
        self.dup_summary.pack(side="left", padx=10)

        cont = ttk.Frame(frame)
        cont.pack(fill="both", expand=True)
        self.dup_tree = ttk.Treeview(cont, columns=("size",), selectmode="extended")
        self.dup_tree.heading("#0", text="مجموعات الملفات المتطابقة", anchor="w")
        self.dup_tree.heading("size", text="الحجم", anchor="e")
        self.dup_tree.column("#0", width=720, anchor="w")
        self.dup_tree.column("size", width=120, anchor="e")
        vsb = ttk.Scrollbar(cont, orient="vertical", command=self.dup_tree.yview)
        self.dup_tree.configure(yscrollcommand=vsb.set)
        self.dup_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.dup_tree.bind("<<TreeviewSelect>>", lambda e: self._show_details_for(self._dup_sel_path()))
        self.dup_tree.bind("<Double-1>", lambda e: self._open_selected())

    def _build_junk_tab(self):
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text="  المخلفات  ")
        top = ttk.Frame(frame, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="فحص المخلفات", command=self._scan_junk).pack(side="left")
        self.junk_summary = ttk.Label(top, text="  ملفات مؤقتة وكاش وسلة المهملات، امنة للحذف عادة")
        self.junk_summary.pack(side="left")

        cont = ttk.Frame(frame)
        cont.pack(fill="both", expand=True)
        self.junk_tree = ttk.Treeview(cont, columns=("size", "type", "path"),
                                      show="headings", selectmode="extended")
        for col, txt, w, anc in (("size", "الحجم", 100, "e"), ("type", "النوع", 200, "w"),
                                 ("path", "الموقع", 640, "w")):
            self.junk_tree.heading(col, text=txt, anchor=anc)
            self.junk_tree.column(col, width=w, anchor=anc)
        vsb = ttk.Scrollbar(cont, orient="vertical", command=self.junk_tree.yview)
        self.junk_tree.configure(yscrollcommand=vsb.set)
        self.junk_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.junk_tree.bind("<<TreeviewSelect>>", lambda e: self._show_details_for(self._junk_sel_path()))

    def _build_details_panel(self, parent):
        panel = ttk.LabelFrame(parent, text="تفاصيل العنصر المحدد", padding=8)
        panel.pack(side="right", fill="y", padx=(8, 0))
        panel.configure(width=320)
        self.details = tk.Text(panel, width=38, height=30, wrap="word",
                               font=("Segoe UI", 9), relief="flat", state="disabled",
                               background="#f7f7f7")
        self.details.pack(fill="both", expand=True)
        self.details.tag_configure("h", font=("Segoe UI", 11, "bold"), foreground="#1a3d6b")
        self.details.tag_configure("safe", font=("Segoe UI", 10, "bold"), foreground="#1b7a1b")
        self.details.tag_configure("unsafe", font=("Segoe UI", 10, "bold"), foreground="#b00000")
        self.details.tag_configure("review", font=("Segoe UI", 10, "bold"), foreground="#b07000")
        self.details.tag_configure("label", font=("Segoe UI", 9, "bold"), foreground="#333")
        self._clear_details()

    def _clear_details(self):
        self.details.config(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", "اختر ملفا او مجلدا من القائمة لعرض تفاصيله هنا.\n\n"
                                    "تظهر لك:\nالنوع والفئة\nالبرنامج التابع له\n"
                                    "الناشر والشركة\nهل امن للحذف")
        self.details.config(state="disabled")

    def _show_details_for(self, path):
        if not path or path == "__RECYCLE__":
            return
        info = self._info_cache.get(path)
        if info is None:
            info = fileinfo.analyze(path)
            self._info_cache[path] = info
        d = self.details
        d.config(state="normal")
        d.delete("1.0", "end")
        d.insert("end", info["name"] + "\n", "h")
        d.insert("end", info["category_label"] + "\n\n")

        def row(label, value):
            if value and value != "-":
                d.insert("end", label + ": ", "label")
                d.insert("end", str(value) + "\n")

        if info["is_dir"] and self.result and path in self.result["dir_sizes"]:
            row("الحجم", human_size(self.result["dir_sizes"][path]) + "  (مجلد)")
        elif not info["is_dir"]:
            row("الحجم", human_size(info["size"]))
        row("النوع", info["type_desc"])
        row("تابع لـ", info["owner"])
        row("الناشر", info["publisher"])
        row("المنتج", info["product"])
        row("الوصف", info["description"])
        row("الاصدار", info["version"])
        row("اخر تعديل", info["modified"])
        row("تاريخ الانشاء", info["created"])
        d.insert("end", "\nالمسار:\n", "label")
        d.insert("end", info["path"] + "\n\n")

        if info["owner_note"]:
            d.insert("end", "ملاحظة: " + info["owner_note"] + "\n\n")

        tag = info["safe"]
        d.insert("end", info["safe_text"] + "\n", tag)
        d.config(state="disabled")

    def _refresh_drives(self):
        for w in self.drives_frame.winfo_children():
            w.destroy()
        drives = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    u = shutil.disk_usage(root)
                    drives.append((root, u.total, u.used, u.free))
                except Exception:
                    continue
        self.drive_combo["values"] = [d[0] for d in drives]
        if drives and not self.drive_var.get():
            self.drive_var.set(drives[0][0])
        for root, total, used, free in drives:
            row = ttk.Frame(self.drives_frame)
            row.pack(fill="x", pady=2)
            pct = (used / total * 100) if total else 0
            ttk.Label(row, text=f"{root}", width=6, font=("Segoe UI", 10, "bold")).pack(side="left")
            ttk.Progressbar(row, mode="determinate", length=260, maximum=100, value=pct).pack(side="left", padx=6)
            state = "ممتلئ" if pct > 90 else ("شبه ممتلئ" if pct > 75 else "جيد")
            ttk.Label(row, text=f"مستخدم {human_size(used)} من {human_size(total)}  "
                                f"| متبقي {human_size(free)}  ({pct:.0f}%)  {state}",
                      font=("Segoe UI", 9)).pack(side="left", padx=6)

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="اختر مجلدا للفحص")
        if folder:
            self.drive_var.set(folder)

    def _start_scan(self):
        target = self.drive_var.get().strip()
        if not target or not os.path.exists(target):
            messagebox.showwarning("تنبيه", "اختر قرصا او مجلدا صحيحا اولا.")
            return
        self.current_root = target
        self.stop_event.clear()
        self.progress_q = queue.Queue()
        self.result = None
        self._info_cache.clear()
        self.tree.delete(*self.tree.get_children())
        self.files_tree.delete(*self.files_tree.get_children())
        self.scan_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.del_btn.config(state="disabled")
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        self.status.config(text=f"جاري فحص {target}")
        engine = ScanEngine(target, self.progress_q, self.stop_event)
        threading.Thread(target=engine.run, daemon=True).start()
        self.after(100, self._poll_queue)

    def _stop_scan(self):
        self.stop_event.set()
        self.status.config(text="جاري الايقاف")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.progress_q.get_nowait()
                if kind == "progress":
                    self.status.config(
                        text=f"فحص {payload['file_count']:,} ملف | {human_size(payload['total_bytes'])} | "
                             f"{payload['current'][:70]}")
                elif kind == "done":
                    self._on_scan_done(payload); return
                elif kind == "cancelled":
                    self._finish_scan("تم الايقاف"); return
                elif kind == "error":
                    self._finish_scan("خطأ اثناء الفحص")
                    messagebox.showerror("خطأ", payload); return
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _finish_scan(self, msg):
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.scan_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.del_btn.config(state="normal")
        self.status.config(text=msg)

    def _on_scan_done(self, payload):
        self.result = payload
        cat = payload.get("cat_bytes", {})
        top_cats = sorted(cat.items(), key=lambda x: x[1], reverse=True)[:3]
        cat_txt = " | ".join(f"{fileinfo._category_label(c)} {human_size(b)}" for c, b in top_cats)
        self._finish_scan(
            f"اكتمل | {payload['file_count']:,} ملف | الاجمالي {human_size(payload['total_bytes'])} || {cat_txt}")
        self._populate_tree()
        self._populate_files()

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        sizes = self.result["dir_sizes"]
        root = self.current_root
        total = sizes.get(root, self.result["total_bytes"]) or 1
        node = self.tree.insert("", "end", text=root,
                                values=(human_size(sizes.get(root, 0)), self._bar(100)), open=True)
        self._tree_nodes = {node: root}
        self._add_children(node, root, total)

    def _bar(self, pct):
        filled = int(round(pct / 10))
        return "#" * filled + "-" * (10 - filled) + f" {pct:.0f}%"

    def _add_children(self, parent_node, parent_path, grand_total):
        sizes = self.result["dir_sizes"]
        subdirs = []
        try:
            for name in os.listdir(parent_path):
                full = os.path.join(parent_path, name)
                if full in sizes and os.path.isdir(full):
                    subdirs.append((full, sizes[full]))
        except (PermissionError, OSError):
            pass
        subdirs.sort(key=lambda x: x[1], reverse=True)
        for full, sz in subdirs:
            if sz == 0:
                continue
            pct = sz / grand_total * 100 if grand_total else 0
            node = self.tree.insert(parent_node, "end", text=os.path.basename(full) or full,
                                    values=(human_size(sz), self._bar(pct)))
            self._tree_nodes[node] = full
            has_sub = any(os.path.join(full, n) in sizes and sizes[os.path.join(full, n)] > 0
                          for n in self._safe_listdir(full))
            if has_sub:
                self.tree.insert(node, "end", text="...")

    @staticmethod
    def _safe_listdir(path):
        try:
            return os.listdir(path)
        except (PermissionError, OSError):
            return []

    def _on_tree_expand(self, event):
        node = self.tree.focus()
        path = self._tree_nodes.get(node)
        if not path:
            return
        children = self.tree.get_children(node)
        if len(children) == 1 and self.tree.item(children[0], "text") == "...":
            self.tree.delete(children[0])
            total = self.result["dir_sizes"].get(self.current_root, 1) or 1
            self._add_children(node, path, total)

    _FILTER_CAT = {
        "وسائط": "media", "مستندات": "doc", "مضغوط": "archive",
        "تنفيذي": "exec", "نظام او برنامج": "system", "مخلفات": "junk",
        "غير مصنف": "other",
    }

    def _populate_files(self):
        self.files_tree.delete(*self.files_tree.get_children())
        if not self.result:
            return
        flt = self.filter_var.get()
        want_cat = self._FILTER_CAT.get(flt)
        shown = 0
        for path, sz in self.result["big_files"]:
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            d = fileinfo._EXT_MAP.get(ext)
            cat = d[1] if d else "other"
            if want_cat and cat != want_cat:
                continue
            cat_label = fileinfo._category_label(cat)
            owner = self._light_owner(path)
            self.files_tree.insert("", "end", values=(human_size(sz), cat_label, owner, path))
            shown += 1
            if shown >= 800:
                break

    @staticmethod
    def _light_owner(path):
        owner, _ = fileinfo._owner_from_path(path)
        return owner or "-"

    def _start_dup_scan(self):
        target = self.current_root or self.drive_var.get().strip()
        if not target or not os.path.exists(target):
            messagebox.showwarning("تنبيه", "اختر قرصا او مجلدا وافحصه اولا.")
            return
        sizes = {"100 KB": 100*1024, "500 KB": 500*1024, "1 MB": 1024*1024,
                 "5 MB": 5*1024*1024, "10 MB": 10*1024*1024, "50 MB": 50*1024*1024}
        min_size = sizes.get(self.dup_min_var.get(), 1024*1024)
        self.dup_tree.delete(*self.dup_tree.get_children())
        self.dup_stop.clear()
        self.dup_q = queue.Queue()
        self.dup_stop_btn.config(state="normal")
        self.dup_summary.config(text="جاري البحث")
        self.status.config(text=f"البحث عن المكررات في {target}")
        eng = DuplicateEngine(target, min_size, self.dup_q, self.dup_stop)
        threading.Thread(target=eng.run, daemon=True).start()
        self.after(120, self._poll_dup)

    def _poll_dup(self):
        try:
            while True:
                kind, payload = self.dup_q.get_nowait()
                if kind == "dup_progress":
                    self.status.config(text=payload)
                elif kind == "dup_done":
                    self._on_dup_done(payload); return
                elif kind == "dup_cancelled":
                    self.dup_stop_btn.config(state="disabled")
                    self.dup_summary.config(text="تم الايقاف")
                    self.status.config(text="تم ايقاف بحث المكررات"); return
                elif kind == "dup_error":
                    self.dup_stop_btn.config(state="disabled")
                    messagebox.showerror("خطأ", payload); return
        except queue.Empty:
            pass
        self.after(120, self._poll_dup)

    def _on_dup_done(self, payload):
        self.dup_stop_btn.config(state="disabled")
        self.dup_groups = payload["groups"]
        wasted = payload["wasted"]
        self.dup_tree.delete(*self.dup_tree.get_children())
        for i, g in enumerate(self.dup_groups, 1):
            saving = g["size"] * (g["count"] - 1)
            parent = self.dup_tree.insert(
                "", "end",
                text=f"مجموعة {i}: {g['count']} نسخ متطابقة، يمكن توفير {human_size(saving)}",
                values=(human_size(g["size"]),), open=False)
            for p in g["paths"]:
                self.dup_tree.insert(parent, "end", text="   " + p, values=(human_size(g["size"]),))
        self.dup_summary.config(
            text=f"وجد {len(self.dup_groups)} مجموعة مكررة، توفير محتمل {human_size(wasted)}")
        self.status.config(text=f"اكتمل بحث المكررات، يمكن توفير {human_size(wasted)}")
        if not self.dup_groups:
            messagebox.showinfo("نتيجة", "لا توجد ملفات مكررة بهذا الحجم.")

    def _scan_junk(self):
        self.junk_tree.delete(*self.junk_tree.get_children())
        candidates = [
            (os.environ.get("TEMP", ""), "ملفات مؤقتة للمستخدم"),
            (r"C:\Windows\Temp", "ملفات مؤقتة للنظام"),
            (os.path.expandvars(r"%LOCALAPPDATA%\Temp"), "ملفات مؤقتة"),
            (os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\INetCache"), "كاش الانترنت"),
            (os.path.expandvars(r"%LOCALAPPDATA%\CrashDumps"), "تفريغات الاعطال"),
            (os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache"), "كاش كروم"),
            (os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache"), "كاش ايدج"),
            (os.path.expandvars(r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles"), "كاش فايرفوكس"),
            (os.path.expandvars(r"%LOCALAPPDATA%\pip\cache"), "كاش بيب"),
            (os.path.expandvars(r"%LOCALAPPDATA%\NVIDIA\DXCache"), "كاش انفيديا"),
            (os.path.expandvars(r"%USERPROFILE%\Downloads"), "التنزيلات راجعها"),
        ]
        seen = set()
        total_junk = 0
        self.status.config(text="جاري فحص المخلفات")
        self.update_idletasks()
        for c, label in candidates:
            if not c:
                continue
            c = os.path.normpath(c)
            if c in seen or not os.path.isdir(c):
                continue
            seen.add(c)
            sz = self._quick_dir_size(c)
            if sz > 0:
                total_junk += sz
                self.junk_tree.insert("", "end", values=(human_size(sz), label, c))
        try:
            rb = self._recycle_bin_size()
            if rb > 0:
                total_junk += rb
                self.junk_tree.insert("", "end",
                                      values=(human_size(rb), "سلة المهملات", "Recycle Bin"))
        except Exception:
            pass
        self.junk_summary.config(text=f"  اجمالي المخلفات القابلة للتنظيف نحو {human_size(total_junk)}")
        self.status.config(text=f"اكتمل فحص المخلفات، نحو {human_size(total_junk)} قابلة للتحرير")

    @staticmethod
    def _quick_dir_size(path, limit_seconds=4):
        total = 0
        start = time.time()
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except (PermissionError, OSError):
                    pass
            if time.time() - start > limit_seconds:
                break
        return total

    @staticmethod
    def _recycle_bin_size():
        total = 0
        for letter in string.ascii_uppercase:
            rb = f"{letter}:\\$Recycle.Bin"
            if os.path.isdir(rb):
                for dirpath, _, filenames in os.walk(rb):
                    for f in filenames:
                        try:
                            total += os.path.getsize(os.path.join(dirpath, f))
                        except (PermissionError, OSError):
                            pass
        return total

    def _tree_sel_path(self):
        sel = self.tree.selection()
        return self._tree_nodes.get(sel[0]) if sel else None

    def _files_sel_path(self):
        sel = self.files_tree.selection()
        if sel:
            vals = self.files_tree.item(sel[0], "values")
            return vals[3] if vals else None
        return None

    def _dup_sel_path(self):
        sel = self.dup_tree.selection()
        if sel:
            txt = self.dup_tree.item(sel[0], "text").strip()
            if txt and not txt.startswith("مجموعة"):
                return txt
        return None

    def _junk_sel_path(self):
        sel = self.junk_tree.selection()
        if sel:
            vals = self.junk_tree.item(sel[0], "values")
            if vals:
                return "__RECYCLE__" if vals[2] == "Recycle Bin" else vals[2]
        return None

    def _selected_paths(self):
        tab = self.nb.index(self.nb.select())
        paths = []
        if tab == 0:
            for n in self.tree.selection():
                p = self._tree_nodes.get(n)
                if p:
                    paths.append(p)
        elif tab == 1:
            for item in self.files_tree.selection():
                vals = self.files_tree.item(item, "values")
                if vals:
                    paths.append(vals[3])
        elif tab == 2:
            for item in self.dup_tree.selection():
                txt = self.dup_tree.item(item, "text").strip()
                if txt and not txt.startswith("مجموعة"):
                    paths.append(txt)
        elif tab == 3:
            for item in self.junk_tree.selection():
                vals = self.junk_tree.item(item, "values")
                if vals:
                    paths.append("__RECYCLE__" if vals[2] == "Recycle Bin" else vals[2])
        return paths

    def _open_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("معلومة", "اختر عنصرا اولا.")
            return
        p = paths[0]
        if p == "__RECYCLE__":
            os.startfile("shell:RecycleBinFolder")
        else:
            open_in_explorer(p)

    def _delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("معلومة", "اختر عنصرا او اكثر للحذف.")
            return
        if "__RECYCLE__" in paths:
            if messagebox.askyesno("تأكيد", "تفريغ سلة المهملات نهائيا؟"):
                self._empty_recycle_bin()
            paths = [p for p in paths if p != "__RECYCLE__"]
            if not paths:
                return
        blocked = [p for p in paths if is_protected(p)]
        if blocked:
            messagebox.showerror("ممنوع",
                "لا يمكن حذف مجلدات النظام الاساسية:\n\n" + "\n".join(blocked[:5]))
            paths = [p for p in paths if not is_protected(p)]
            if not paths:
                return
        risky = [p for p in paths if self._is_system_file(p)]
        if risky:
            if not messagebox.askyesno("تحذير",
                    "العناصر التالية تبدو تابعة للنظام او لبرنامج مثبت:\n\n"
                    + "\n".join(risky[:5]) +
                    "\n\nحذفها قد يعطل برنامجا او النظام. الافضل ازالتها من ازالة البرامج.\n\n"
                    "متأكد تريد الاستمرار؟", icon="warning"):
                return
        total = 0
        for p in paths:
            if self.result and p in self.result["dir_sizes"]:
                total += self.result["dir_sizes"][p]
            elif os.path.isfile(p):
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
        mode = "سيتم نقلها الى سلة المهملات وتبقى قابلة للاستعادة" if HAS_SEND2TRASH \
            else "سيتم حذفها نهائيا وغير قابلة للاستعادة"
        preview = "\n".join(f"  {p}" for p in paths[:8])
        if len(paths) > 8:
            preview += f"\n  و {len(paths) - 8} عنصر اخر"
        if not messagebox.askyesno("تأكيد الحذف",
                f"عدد العناصر: {len(paths)}\nالحجم التقريبي: {human_size(total)}\n\n"
                f"{preview}\n\n{mode}\n\nهل انت متأكد؟", icon="warning"):
            return
        errors, deleted = [], 0
        for p in paths:
            try:
                if HAS_SEND2TRASH:
                    send2trash(os.path.normpath(p))
                else:
                    self._hard_delete(p)
                deleted += 1
            except Exception as e:
                errors.append(f"{p}: {e}")
        self._remove_deleted_from_views(paths)
        self._refresh_drives()
        msg = f"تم حذف {deleted} عنصر، حرر نحو {human_size(total)}."
        if errors:
            msg += f"\nفشل {len(errors)} عنصر، قد تحتاج صلاحيات مدير."
            messagebox.showwarning("اكتمل مع اخطاء", msg + "\n\n" + "\n".join(errors[:5]))
        else:
            messagebox.showinfo("تم", msg)
        self.status.config(text=msg)

    @staticmethod
    def _is_system_file(path):
        low = os.path.normpath(path).lower()
        win = os.environ.get("SystemRoot", r"C:\Windows").lower()
        if low.startswith(win) or "program files" in low:
            if not fileinfo._is_junk_location(path):
                return True
        return False

    @staticmethod
    def _hard_delete(path):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=False)
        else:
            os.remove(path)

    def _remove_deleted_from_views(self, paths):
        pset = set(os.path.normpath(p) for p in paths)
        for item in self.files_tree.get_children():
            vals = self.files_tree.item(item, "values")
            if vals and os.path.normpath(vals[3]) in pset:
                self.files_tree.delete(item)
        for parent in self.dup_tree.get_children():
            for child in self.dup_tree.get_children(parent):
                txt = self.dup_tree.item(child, "text").strip()
                if txt and os.path.normpath(txt) in pset:
                    self.dup_tree.delete(child)
        for item in self.junk_tree.get_children():
            self.junk_tree.delete(item)
        for node, p in list(getattr(self, "_tree_nodes", {}).items()):
            if os.path.normpath(p) in pset:
                try:
                    self.tree.delete(node)
                except tk.TclError:
                    pass

    def _empty_recycle_bin(self):
        try:
            flags = 0x01 | 0x02 | 0x04
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
            messagebox.showinfo("تم", "تم تفريغ سلة المهملات.")
            self._refresh_drives()
        except Exception as e:
            messagebox.showerror("خطأ", f"تعذر تفريغ السلة:\n{e}")


def main():
    app = App()
    if not HAS_SEND2TRASH:
        app.after(500, lambda: messagebox.showwarning(
            "تنبيه", "مكتبة send2trash غير مثبتة.\nالحذف سيكون نهائيا.\n"
                     "للتثبيت: pip install send2trash"))
    app.mainloop()


if __name__ == "__main__":
    main()
