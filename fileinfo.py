import os
import ctypes
from ctypes import wintypes
import datetime


_EXT_MAP = {
    "exe": ("برنامج تنفيذي", "exec"),
    "msi": ("حزمة تثبيت", "exec"),
    "dll": ("مكتبة نظام او برنامج", "system"),
    "sys": ("ملف نظام تعريف", "system"),
    "drv": ("تعريف جهاز", "system"),
    "bat": ("سكربت اوامر", "exec"),
    "cmd": ("سكربت اوامر", "exec"),
    "ps1": ("سكربت بوويرشيل", "exec"),
    "com": ("برنامج تنفيذي", "exec"),
    "scr": ("شاشة توقف او تنفيذي", "exec"),
    "tmp": ("ملف مؤقت", "junk"),
    "temp": ("ملف مؤقت", "junk"),
    "log": ("سجل احداث", "junk"),
    "cache": ("ملف كاش", "junk"),
    "dmp": ("ملف تفريغ ذاكرة", "junk"),
    "old": ("نسخة قديمة", "junk"),
    "bak": ("نسخة احتياطية", "junk"),
    "chk": ("بقايا فحص القرص", "junk"),
    "etl": ("سجل تتبع نظام", "junk"),
    "mp4": ("فيديو", "media"), "mkv": ("فيديو", "media"), "avi": ("فيديو", "media"),
    "mov": ("فيديو", "media"), "wmv": ("فيديو", "media"), "flv": ("فيديو", "media"),
    "webm": ("فيديو", "media"), "m4v": ("فيديو", "media"), "ts": ("فيديو", "media"),
    "mp3": ("صوت", "media"), "wav": ("صوت", "media"), "flac": ("صوت", "media"),
    "aac": ("صوت", "media"), "ogg": ("صوت", "media"), "wma": ("صوت", "media"),
    "m4a": ("صوت", "media"),
    "jpg": ("صورة", "media"), "jpeg": ("صورة", "media"), "png": ("صورة", "media"),
    "gif": ("صورة", "media"), "bmp": ("صورة", "media"), "webp": ("صورة", "media"),
    "tiff": ("صورة", "media"), "svg": ("صورة", "media"), "ico": ("ايقونة", "media"),
    "raw": ("صورة خام", "media"), "psd": ("ملف فوتوشوب", "media"),
    "pdf": ("مستند بي دي اف", "doc"), "doc": ("مستند وورد", "doc"), "docx": ("مستند وورد", "doc"),
    "xls": ("جدول اكسل", "doc"), "xlsx": ("جدول اكسل", "doc"),
    "ppt": ("عرض باوربوينت", "doc"), "pptx": ("عرض باوربوينت", "doc"),
    "txt": ("نص", "doc"), "rtf": ("مستند منسق", "doc"), "csv": ("بيانات جدولية", "doc"),
    "odt": ("مستند", "doc"), "epub": ("كتاب الكتروني", "doc"),
    "zip": ("ارشيف مضغوط", "archive"), "rar": ("ارشيف مضغوط", "archive"),
    "7z": ("ارشيف مضغوط", "archive"), "tar": ("ارشيف", "archive"),
    "gz": ("ارشيف مضغوط", "archive"), "iso": ("صورة قرص", "archive"),
    "cab": ("ارشيف ويندوز", "archive"),
    "py": ("كود بايثون", "code"), "js": ("كود جافاسكربت", "code"),
    "java": ("كود جافا", "code"), "c": ("كود سي", "code"), "cpp": ("كود سي بلس", "code"),
    "cs": ("كود سي شارب", "code"), "html": ("صفحة ويب", "code"), "css": ("ستايل", "code"),
    "json": ("بيانات جيسون", "code"), "xml": ("بيانات اكس ام ال", "code"),
    "vmdk": ("قرص ظاهري", "vm"), "vhd": ("قرص ظاهري", "vm"), "vhdx": ("قرص ظاهري", "vm"),
    "pak": ("بيانات لعبة", "game"), "vpk": ("بيانات لعبة", "game"),
}


def _category_label(cat):
    return {
        "exec": "برنامج تنفيذي",
        "system": "ملف نظام او برنامج",
        "junk": "ملف مؤقت او مخلفات",
        "media": "وسائط",
        "doc": "مستند",
        "archive": "ملف مضغوط",
        "code": "ملف برمجي",
        "vm": "قرص ظاهري",
        "game": "بيانات لعبة",
        "other": "غير مصنف",
    }.get(cat, "غير مصنف")


def get_publisher_info(path):
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return {}
        ptr = ctypes.c_void_p()
        ln = wintypes.UINT()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p(r"\VarFileInfo\Translation"),
                                  ctypes.byref(ptr), ctypes.byref(ln)) or not ln.value:
            return {}
        words = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
        lang, cp = words[0], words[1]
        info = {}
        for key in ("CompanyName", "ProductName", "FileDescription", "FileVersion"):
            sub = "\\StringFileInfo\\%04x%04x\\%s" % (lang, cp, key)
            p2 = ctypes.c_void_p()
            l2 = wintypes.UINT()
            if ver.VerQueryValueW(buf, ctypes.c_wchar_p(sub),
                                  ctypes.byref(p2), ctypes.byref(l2)) and l2.value:
                val = ctypes.wstring_at(p2, l2.value).strip("\x00 ")
                if val:
                    info[key] = val
        return info
    except Exception:
        return {}


def _owner_from_path(path):
    np = os.path.normpath(path)
    low = np.lower()
    parts = np.split(os.sep)

    def after(marker):
        ml = marker.lower()
        for i, p in enumerate(parts):
            if p.lower() == ml and i + 1 < len(parts):
                return parts[i + 1]
        return None

    win = os.environ.get("SystemRoot", r"C:\Windows").lower()
    if low.startswith(win):
        return ("نظام التشغيل ويندوز", "هذا ملف تابع لنظام ويندوز، لا تحذفه.")

    for marker in ("Program Files (x86)", "Program Files"):
        if marker.lower() in low:
            prog = after(marker)
            if prog:
                return (f"برنامج مثبت: {prog}",
                        "ملف تابع لبرنامج مثبت، احذفه من خلال ازالة البرامج وليس يدويا.")

    if "programdata" in low:
        prog = after("ProgramData")
        if prog and prog.lower() not in ("microsoft", "package cache"):
            return (f"بيانات برنامج: {prog}", "بيانات مشتركة لبرنامج مثبت.")

    if "appdata" in low:
        for kind in ("Local", "LocalLow", "Roaming"):
            prog = after(kind)
            if prog and prog.lower() not in ("temp", "microsoft", "packages"):
                return (f"اعدادات او كاش برنامج: {prog}",
                        "ملف اعدادات او كاش لبرنامج، غالبا يعاد انشاؤه تلقائيا.")
        return ("بيانات تطبيقات المستخدم", "ملف ضمن مجلد بيانات التطبيقات.")

    user = os.environ.get("USERPROFILE", "").lower()
    if user and low.startswith(user):
        folder = after(os.path.basename(user))
        known = {
            "downloads": "التنزيلات", "desktop": "سطح المكتب",
            "documents": "المستندات", "pictures": "الصور",
            "videos": "الفيديوهات", "music": "الموسيقى",
        }
        if folder and folder.lower() in known:
            return (f"ملفاتك الشخصية ({known[folder.lower()]})",
                    "ملف من ملفاتك الشخصية، راجعه قبل الحذف.")
        return ("ملفاتك الشخصية", "ملف ضمن مجلد المستخدم.")

    return (None, None)


def _is_junk_location(path):
    low = os.path.normpath(path).lower()
    markers = ("\\temp\\", "\\tmp\\", "\\inetcache\\", "\\cache\\",
               "\\crashdumps\\", "\\$recycle.bin\\", "\\windows\\temp\\",
               "\\webcache\\", "\\code cache\\", "\\gpucache\\")
    return any(m in low for m in markers)


def analyze(path):
    info = {
        "name": os.path.basename(path) or path,
        "path": path,
        "exists": os.path.exists(path),
        "is_dir": os.path.isdir(path),
        "size": 0, "modified": "-", "created": "-",
        "ext": "", "type_desc": "غير معروف", "category": "other",
        "category_label": _category_label("other"),
        "owner": "غير معروف", "owner_note": "",
        "publisher": "", "product": "", "description": "", "version": "",
        "safe": "review",
        "safe_text": "راجع قبل الحذف",
    }
    if not info["exists"]:
        info["type_desc"] = "غير موجود ربما حذف"
        return info

    try:
        st = os.stat(path)
        info["size"] = st.st_size if not info["is_dir"] else 0
        info["modified"] = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        info["created"] = datetime.datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        pass

    if info["is_dir"]:
        info["type_desc"] = "مجلد"
        info["category"] = "folder"
        info["category_label"] = "مجلد"
    else:
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        info["ext"] = ext
        desc, cat = _EXT_MAP.get(ext, (f"ملف {ext}" if ext else "ملف بدون امتداد", "other"))
        info["type_desc"] = desc
        info["category"] = cat
        info["category_label"] = _category_label(cat)

        if ext in ("exe", "dll", "sys", "msi", "ocx", "scr"):
            pub = get_publisher_info(path)
            info["publisher"] = pub.get("CompanyName", "")
            info["product"] = pub.get("ProductName", "")
            info["description"] = pub.get("FileDescription", "")
            info["version"] = pub.get("FileVersion", "")

    owner, note = _owner_from_path(path)
    if owner:
        info["owner"] = owner
        info["owner_note"] = note
    elif info["publisher"]:
        info["owner"] = info["publisher"]

    win = os.environ.get("SystemRoot", r"C:\Windows").lower()
    low = os.path.normpath(path).lower()
    if _is_junk_location(path) or info["category"] == "junk":
        info["safe"] = "safe"
        info["safe_text"] = "امن للحذف، ملف مؤقت او كاش يعاد انشاؤه"
    elif low.startswith(win) or "program files" in low:
        info["safe"] = "unsafe"
        info["safe_text"] = "لا تحذفه، ملف نظام او برنامج، استخدم ازالة البرامج"
    elif info["category"] in ("media", "doc"):
        info["safe"] = "review"
        info["safe_text"] = "ملفك الشخصي، راجعه واحذفه اذا ما تحتاجه"
    else:
        info["safe"] = "review"
        info["safe_text"] = "راجع قبل الحذف"

    return info
