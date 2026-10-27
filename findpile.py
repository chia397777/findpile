#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
findpile — 本機檔案關鍵字搜尋工具

為「一個資料夾裡堆了幾千幾萬份 Word / PDF / 掃描圖片」的情況設計。
完全離線執行,不呼叫任何 API,檔案內容不離開你的電腦。

兩段式設計:
  1. index  掃一次全部檔案,把內文抽出來存進 SQLite(之後只更新有變動的)
  2. find   查關鍵字,依「密度」排序,附上前後文摘要,輸出 Markdown 報告

用法:
  python findpile.py index                      建立/更新索引
  python findpile.py index --full                全部重建
  python findpile.py find 報價                   查單一關鍵字
  python findpile.py find 報價 估價              多關鍵字(預設 OR)
  python findpile.py find 會議 紀錄 --mode all    必須同時出現
  python findpile.py find 報價 --top 30 --ctx 4   前 30 名、每檔 4 段前後文
  python findpile.py find 報價 --no-report        只看終端機,不產生報告檔
  python findpile.py stats                       看索引狀態
  python findpile.py ocr --scope 掃描件           對掃描圖片做 OCR
  python findpile.py imgindex                    建立以圖搜圖的特徵庫
  python findpile.py imgfind 照片.jpg            以圖搜圖

排序邏輯(重點):
  分數 = 命中次數 / (檔案文字量KB ** 0.75) + 檔名命中加成
  這樣「32KB 的報告出現 7 次」會排在「2MB 的法規全文出現 58 次」前面,
  因為前者才是真正以該關鍵字為主題的檔案。細節見 README。
"""

import argparse
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------- 平台差異

# Windows 主控台預設是 cp950,印到編不出來的字元(例如 📌、▸)會丟
# UnicodeEncodeError 讓整個索引中途崩潰 —— 跑了幾小時的 OCR 就白費了。
# 改成用 ? 取代,寧可字醜也不要中斷。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


class NotHere(Exception):
    """原始檔不在這台機器上(只複製了索引過來的情形)。"""


def _resolve(path):
    local = localize(path)
    if not os.path.exists(local):
        raise NotHere(path)
    return local


def open_path(path):
    """用系統預設程式開啟檔案。"""
    p = _resolve(path)
    if IS_WIN:
        os.startfile(p)
    elif IS_MAC:
        subprocess.run(["open", p], check=False)
    else:
        subprocess.run(["xdg-open", p], check=False)


def reveal_path(path):
    """在檔案管理員(檔案總管 / Finder)裡顯示並選取這個檔案。"""
    p = os.path.normpath(_resolve(path))
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", p])
    elif IS_MAC:
        subprocess.run(["open", "-R", p], check=False)
    else:
        subprocess.run(["xdg-open", os.path.dirname(p)], check=False)


def _rel(*parts):
    """用本平台的分隔符組相對路徑(原本寫死反斜線,Mac 上會變成檔名的一部分)。"""
    return os.path.join(*parts)


# ---------------------------------------------------------------- 設定檔

def load_config():
    r"""
    讀 config.json(可有可無)。所有設定都有預設值,不建這個檔也能跑。

    最常用的情形是「索引在這台、原始檔在別台」:索引裡存的是建索引
    那台機器的絕對路徑(例 D:/文件/...),搬到另一台之後要嘛對應到
    新位置,要嘛就只能查不能開。

      {
        "data_dir":   "D:/文件/_findpile",      索引與報告放哪
        "scan_root":  "D:/文件",                index 預設掃哪
        "index_root": "D:/文件",                顯示路徑時要去掉的前綴
        "path_map":   {"D:/文件": "/Users/me/Documents/文件"},   前綴對應
        "files_here": false,                     這台有沒有原始檔
        "ocr_scopes": {"掃描件": ["掃描/2024"]}   --scope 的命名捷徑
      }

    完整說明見 config.example.json。
    """
    f = os.path.join(HERE, "config.json")
    if not os.path.exists(f):
        return {}
    try:
        import json
        return json.loads(io.open(f, encoding="utf-8").read())
    except Exception as e:
        print("config.json 讀取失敗,忽略:%s" % e)
        return {}


def _sep_norm(s):
    r"""
    比對路徑前綴之前先正規化:反斜線換成斜線、去掉尾端分隔符、轉小寫。

    為什麼需要:index_root 這類設定是人手打的,同一個資料夾可能寫成
    "D:\\文件"、"D:/文件" 或 "D:\\文件\\"。以前逐字面比對,寫成正斜線
    就一律比不到 —— 而且是靜默失敗:is_borrowed_root() 會回 False,
    index 的刪除守門整個消失。

    replace 不改變長度,所以呼叫端用 len(pre) 做切片位移仍然正確。
    """
    return s.replace("\\", "/").rstrip("/").lower()


def strip_root(path, root):
    """
    把絕對路徑縮成相對路徑,純字串處理。
    不能用 os.path.relpath —— 索引裡是 Windows 路徑,在 Mac 上
    posixpath 不認反斜線,relpath 會算出一串 ../../.. 的垃圾。
    """
    if not root:
        return path
    pre = _sep_norm(root)
    return path[len(pre) + 1:] if _sep_norm(path).startswith(pre + "/") else path


def fmt_size(n):
    """1536000 -> '1.5 MB'。影片動輒幾百 MB,用位元組顯示沒人看得懂。"""
    n = n or 0
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= step:
            return "%.1f %s" % (n / step, unit)
    return "%d B" % n


def split_display(rel):
    """切成 (資料夾, 檔名)。反斜線與斜線都認,因為索引可能來自另一個平台。"""
    i = max(rel.rfind("\\"), rel.rfind("/"))
    return (rel[:i], rel[i + 1:]) if i >= 0 else ("", rel)


def _under(path, root):
    """path 是否位於 root 底下。純字串比對,因為索引可能來自另一個平台。"""
    if not root:
        return False
    return _sep_norm(path).startswith(_sep_norm(root) + "/")


def strip_root_any(path, roots):
    """從多個根目錄裡挑最長的那個去掉(巢狀根目錄時才會有差)。"""
    best = ""
    for r in roots:
        if _under(path, r) and len(r) > len(best):
            best = r
    return strip_root(path, best) if best else path


def root_label(path, roots):
    """這個檔案屬於哪個根目錄,取最後一段當標籤(多根目錄時顯示用)。"""
    best = ""
    for r in roots:
        if _under(path, r) and len(r) > len(best):
            best = r
    if not best:
        return ""
    return split_display(best.rstrip("\\/"))[1] or best


def localize(path):
    """依 config 的 path_map 把索引裡的路徑換成這台機器的實際位置。"""
    for src, dst in (CONFIG.get("path_map") or {}).items():
        pre = _sep_norm(src)
        if _sep_norm(path).startswith(pre + "/"):
            tail = path[len(pre) + 1:].replace("\\", "/").split("/")
            return os.path.join(dst, *tail)
    return path


# ---------------------------------------------------------------- 設定

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = load_config()        # 要在 HERE 之後才算得出來


def _cfg_path(key, fallback):
    """config.json 裡的路徑設定,支援 ~ 展開。"""
    v = CONFIG.get(key)
    return os.path.abspath(os.path.expanduser(v)) if v else fallback


# 程式碼和資料刻意可以分開放:程式碼放 git 倉庫,索引(含客戶文件全文)
# 留在本機。config.json 不進 git,所以兩台機器各自指向自己的位置。
DATA_DIR = _cfg_path("data_dir", HERE)
DB_PATH = _cfg_path("db_path", os.path.join(DATA_DIR, "index.db"))
REPORT_DIR = _cfg_path("report_dir", os.path.join(DATA_DIR, "reports"))
DEFAULT_ROOT = _cfg_path("scan_root", os.path.dirname(HERE))

# 會抽內文的副檔名
EXT_OOXML = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm", ".dotx", ".potx"}
EXT_OLE2 = {".doc", ".xls", ".ppt", ".dot"}   # Word97/Excel97/PPT97 二進位格式
EXT_PDF = {".pdf"}
EXT_HTML = {".html", ".htm", ".xhtml"}
EXT_PLAIN = {".txt", ".md", ".json", ".csv", ".tsv", ".jsx", ".rtf"}
# 刻意不含 .py/.js/.xml/.log —— 這個資料夾裡有數千個程式庫檔案,會把搜尋結果洗掉。
# 真要一起查:--include-code
EXT_ODF = {".odt", ".ods", ".odp"}
EXT_ALL = EXT_OOXML | EXT_OLE2 | EXT_PDF | EXT_HTML | EXT_PLAIN | EXT_ODF

# 不掃的目錄
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__",
             ".claude", "_findpile", "$RECYCLE.BIN", "System Volume Information",
             # --- macOS ---
             "Library",          # 應用程式資料與快取,幾十萬個檔案,沒有要搜的東西
             "Applications", "Applications (Parallels)",
             ".Trash", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100"}

# 這些是 macOS 的「套件」—— 看起來是一個檔案,實際上是資料夾。
# 不排除的話會爬進上萬個內部資源檔。
SKIP_SUFFIX = (".app", ".photoslibrary", ".musiclibrary", ".tvlibrary",
               ".imovielibrary", ".theater", ".fcpbundle", ".sparsebundle",
               ".framework", ".lproj", ".xcodeproj", ".playground", ".band",
               ".logicx", ".pkg", ".bundle",
               # iWork 與 RTFD 在檔案系統上其實是資料夾。爬進去只會抽到
               # 一堆叫 TXT.rtf / index.xml 的內部檔,看不出原本是哪份文件。
               ".pages", ".numbers", ".key", ".rtfd", ".scriv")

# `index --mac` 的預設範圍。不整顆 ~ 掃是因為 ~/Library 底下有幾十萬個
# 應用程式檔案,掃它只是浪費時間。iCloud 雲碟在 Library 底下,所以
# 要單獨列出來(否則會被上面的 Library 規則擋掉)。
MAC_DIRS = [os.path.expanduser(d) for d in (
    "~/Documents",
    "~/Desktop",
    "~/Downloads",
    "~/Library/Mobile Documents/com~apple~CloudDocs",      # iCloud 雲碟
)]

MAX_BYTES = 80 * 1024 * 1024    # 超過這個大小的檔案跳過
MAX_CHARS = 3_000_000           # 每檔最多存這麼多字,避免索引暴肥
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

# \u5224\u65b7\u300c\u9019\u6bb5\u662f\u4e0d\u662f\u771f\u4e2d\u6587\u300d\u7528\u7684\u5e38\u7528\u5b57\u8868\u3002
# \u820a\u7248 .doc \u4ee5 UTF-16LE \u8aa4\u89e3 ASCII \u6642\u6703\u5192\u51fa\u4e00\u5806\u7f55\u7528\u6f22\u5b57(\u4f8b:"Microsoft Office" \u2192 \u694d\u7263\u736f\u666f\u2074\u664f\u6966\u6563),
# \u9019\u7a2e\u5047\u4e2d\u6587\u4e0d\u542b\u5e38\u7528\u5b57\u3001\u4e5f\u4e0d\u542b\u4e2d\u6587\u6a19\u9ede,\u9760\u9019\u5f35\u8868\u5c31\u6ffe\u5f97\u6389\u3002
_COMMON = set(
    "\u7684\u4e00\u662f\u4e0d\u4e86\u5728\u4eba\u6709\u6211\u4ed6\u9019\u4e2d\u5927\u70ba\u4e0a\u500b\u570b\u4ee5\u8981\u6642\u4f86\u7528\u5011\u751f\u5230\u4f5c\u5730\u65bc\u51fa\u5c31\u5206\u5c0d\u6210\u6703\u53ef\u4e3b"
    "\u767c\u5e74\u52d5\u540c\u5de5\u4e5f\u80fd\u4e0b\u904e\u5b50\u8aaa\u7522\u7a2e\u9762\u800c\u65b9\u5f8c\u591a\u5b9a\u884c\u5b78\u6cd5\u81ea\u7406\u8d77\u5c0f\u7269\u73fe\u5be6\u52a0\u91cf\u90fd\u5169\u9ad4\u5236\u7576"
    "\u4f7f\u9ede\u5f9e\u696d\u672c\u53bb\u628a\u6027\u597d\u61c9\u958b\u5b83\u5408\u9084\u56e0\u7531\u5176\u4e9b\u7136\u524d\u5916\u5929\u653f\u4e09\u8207\u95dc\u5404\u91cd\u65b0\u7dda\u5167\u6578\u6b63\u5fc3\u53cd\u4f60"
    "\u660e\u770b\u539f\u53c8\u5229\u6bd4\u6216\u4f46\u8cea\u6c23\u7b2c\u5411\u9053\u547d\u6b64\u8b8a\u689d\u53ea\u6c92\u7d50\u89e3\u554f\u610f\u5efa\u6708\u516c\u7121\u7cfb\u8ecd\u5f88\u60c5\u8005\u6700\u7acb\u4ee3\u60f3"
    "\u5df2\u901a\u4e26\u63d0\u76f4\u984c\u7a0b\u5c55\u679c\u6599\u8c61\u54e1\u4f4d\u5165\u5e38\u6587\u7e3d\u6b21\u54c1\u5f0f\u6d3b\u8a2d\u53ca\u7ba1\u7279\u4ef6\u9577\u6c42\u8001\u982d\u57fa\u8cc7\u908a\u6d41\u8def\u7d1a"
    "\u5c11\u5716\u5c71\u7d71\u63a5\u77e5\u8f03\u5c07\u7d44\u898b\u8a08\u5225\u5979\u624b\u89d2\u671f\u6839\u8ad6\u904b\u6307\u5e7e\u5340\u5f37\u653e\u6c7a\u897f\u88ab\u5e79\u505a\u5fc5\u6230\u5148\u56de\u5247\u4efb\u53d6"
    "\u64da\u8655\u968a\u5357\u7d66\u8272\u5149\u9580\u5373\u4fdd\u6cbb\u5317\u9020\u767e\u898f\u71b1\u9818\u6d77\u53e3\u6771\u5c0e\u5668\u58d3\u5fd7\u4e16\u91d1\u589e\u722d\u6fdf\u968e\u6cb9\u601d\u8853\u6975\u4ea4\u53d7"
    "\u806f\u8a8d\u5171\u6b0a\u6536\u8b49\u6539\u6e05\u5df1\u7f8e\u518d\u63a1\u8f49\u66f4\u55ae\u98a8\u5207\u6253\u767d\u6559\u901f\u82b1\u5e36\u5b89\u5834\u8eab\u8eca\u4f8b\u771f\u52d9\u5177\u842c\u6bcf\u76ee\u81f3\u9054"
    "\u8d70\u7a4d\u793a\u8b70\u8072\u5831\u9b25\u5e95\u8a34\u544a\u9662\u5211\u6c11\u9805\u6b3e\u8a1f\u5be9\u5224\u88c1\u6c7a\u72c0\u8acb\u76ca\u8cac\u4efb\u640d\u5bb3\u8ce0\u511f\u5951\u7d04\u8ca1\u72af\u7f6a\u88ab\u5bb3"
    "\u4e4b\u4e4e\u8005\u4e5f\u77e3\u7109\u54c9\u6c11\u6cd5\u9662\u5b57\u865f\u7b2c\u9805\u689d"
)
_PUNCT = set("\uff0c\u3002\u3001\uff1a\uff1b\uff01\uff1f\uff08\uff09\u300c\u300d\u300e\u300f\u3014\u3015\u300a\u300b\u3008\u3009\u2014\u2026\u00b7")
# Word \u5167\u90e8\u6a23\u5f0f\u540d\uff0fOLE \u4e32\u6d41\u540d:\u662f\u683c\u5f0f\u8cc7\u6599,\u4e0d\u662f\u5167\u5bb9
_JUNK = re.compile(
    r"^(\u9810\u8a2d\u6bb5\u843d\u5b57\u578b|\u5167\u6587|\u8868\u683c\u5167\u6587|\u8a3b\u89e3\u65b9\u584a\u6587\u5b57|\u9801\u9996|\u9801\u5c3e|\u6e05\u55ae\u6bb5\u843d|\u6a19\u984c\s*\d*|\u7d14\u6587\u5b57|"
    r"Data|\d*Table|WordDocument|SummaryInformation|DocumentSummaryInformation|"
    r"CompObj|ObjectPool|Root ?Entry|MSWordDoc|Word\.Document.*|Normal\.dotm?|"
    r"Microsoft .*(Word|Excel|PowerPoint|Office).*|Times New Roman|Arial|Calibri|"
    r"\u65b0\u7d30\u660e\u9ad4|\u6a19\u6977\u9ad4|\u7d30\u660e\u9ad4|\u5fae\u8edf\u6b63\u9ed1\u9ad4|Wingdings|Symbol)\s*$", re.I)


def _looks_like_text(seg):
    """\u771f\u4e2d\u6587/\u82f1\u6587\u6bb5\u843d \u2192 True;\u4e8c\u9032\u4f4d\u4e82\u78bc \u2192 False\u3002"""
    if len(seg) < 6 or _JUNK.match(seg):
        return False
    cjk = CJK.findall(seg)
    if cjk:
        # \u4e2d\u6587\u6bb5\u843d:\u8981\u561b\u6709\u4e2d\u6587\u6a19\u9ede,\u8981\u561b\u5e38\u7528\u5b57\u4f54\u4e00\u5b9a\u6bd4\u4f8b
        if any(c in _PUNCT for c in seg):
            return True
        return sum(1 for c in cjk if c in _COMMON) / len(cjk) >= 0.30
    # \u82f1\u6587\u6bb5\u843d:\u5b57\u6bcd\u8207\u7a7a\u767d\u8981\u4f54\u591a\u6578
    ok = sum(1 for c in seg if c.isascii() and (c.isalnum() or c in " ,.()[]:;-/%'\"&"))
    return ok / len(seg) >= 0.85


# ---------------------------------------------------------------- 工具

def _console():
    """Windows 終端機預設 cp950,改成 UTF-8 才能印中文不報錯。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _squeeze(t):
    """壓掉控制字元與多餘空白。"""
    t = t.replace("\u3000", " ")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


#   中日韓漢字       相容漢字       中文標點      全角     注音     ASCII
_ALLOW = re.compile(r"[^一-鿿豈-﫿　-〿！-￮"
                    r"ㄅ-ㄯ\n\t\x20-\x7e]+")


def _denoise(t):
    """
    舊版 .doc 直接解碼必然夾帶二進位亂碼,且亂碼常和真文字黏在一起。
    先做字元級白名單(把不可能出現在中文文書裡的碼位換成分隔符),
    再逐段判斷只留真文字,最後去掉重複片段。
    """
    keep, seen = [], set()
    for seg in _ALLOW.sub("\x00", t).split("\x00"):
        seg = seg.strip()
        if not _looks_like_text(seg):
            continue
        k = seg[:50]
        if k in seen:
            continue
        seen.add(k)
        keep.append(seg)
    return "\n".join(keep)


def _strip_tags(t):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = re.sub(r"<[^>]+>", "\n", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return t


# 用 utf-16 / utf-32(不帶 -le/-be)—— 這兩個 codec 會自己讀 BOM 判斷
# 位元組序並且把 BOM 吃掉。寫成 utf-16-le 的話 U+FEFF 會留在內文第一個字,
# 檔首的關鍵字就比對不到。
# 順序有意義:UTF-32-LE 的 BOM 以 \xff\xfe 開頭,必須排在 UTF-16-LE 前面。
_BOMS = ((b"\xef\xbb\xbf", "utf-8-sig"),
         (b"\xff\xfe\x00\x00", "utf-32"),
         (b"\x00\x00\xfe\xff", "utf-32"),
         (b"\xff\xfe", "utf-16"),
         (b"\xfe\xff", "utf-16"))


def _decode_best(raw, encodings=("big5", "cp950", "gb18030", "utf-16-le", "utf-16-be")):
    """
    判斷純文字檔的編碼。兩個陷阱:

    1. 不能單純比「哪個編碼解出的漢字最多」。UTF-8 的中文用 Big5 去解,
       3 個 byte 會變成大約 1.5 個漢字 —— 亂碼的漢字數反而比正確答案多,
       正確答案就輸掉了。所以嚴格 UTF-8 解得過就直接採用:Big5 的
       位元組序列幾乎不可能剛好是合法的 UTF-8。

    2. 剩下的編碼之間要比「常用字比例」而不是漢字數量。亂碼產生的是
       一串罕用字(閫欐槸涔欒硣鏂欏ぞ…),真中文則以常用字為主。
    """
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc, "ignore")
    try:
        return raw.decode("utf-8")          # 嚴格模式:過了就是 UTF-8
    except UnicodeDecodeError:
        pass

    best, best_score = "", -1
    for enc in encodings:
        try:
            t = raw.decode(enc, "ignore")
        except Exception:
            continue
        cjk = CJK.findall(t)
        common = sum(1 for c in cjk if c in _COMMON)
        score = (common * 6                                  # 常用字最有說服力
                 + len(cjk)
                 + sum(1 for c in t[:200000]
                       if c.isascii() and c.isprintable()))
        if score > best_score:
            best, best_score = t, score
    return best


# ---------------------------------------------------------------- 各格式抽文

def read_ooxml(path):
    """docx / pptx / xlsx —— 本質是 zip,直接讀 XML 去標籤最快也最耐壞檔。"""
    parts = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(
            r"word/(document|footnotes|endnotes|comments)\.xml$"
            r"|word/header\d*\.xml$|word/footer\d*\.xml$"
            r"|ppt/slides/slide\d+\.xml$|ppt/notesSlides/notesSlide\d+\.xml$"
            r"|xl/sharedStrings\.xml$|xl/worksheets/sheet\d+\.xml$", n)]
        names.sort(key=lambda s: (len(s), s))
        for n in names:
            x = z.read(n).decode("utf-8", "ignore")
            x = re.sub(r"</a:p>|</w:p>|</w:tr>|</row>", "\n", x)
            x = re.sub(r"</w:tc>|</c>", "\t", x)
            parts.append(re.sub(r"<[^>]+>", "", x))
    return _squeeze("\n".join(parts))


def read_odf(path):
    with zipfile.ZipFile(path) as z:
        x = z.read("content.xml").decode("utf-8", "ignore")
    x = re.sub(r"</text:p>|</table:table-row>", "\n", x)
    return _squeeze(re.sub(r"<[^>]+>", "", x))


def read_ole2(path):
    """
    Word97/.doc 等 OLE2 二進位檔。沒裝 olefile 也能處理:
    Word 97+ 內文多為 UTF-16LE,舊檔或部分片段是 Big5/cp950。
    兩種都解,取中文較多者,再過濾亂碼。
    """
    raw = open(path, "rb").read()
    cands = []
    for enc in ("utf-16-le", "cp950", "big5"):
        try:
            cands.append(_denoise(raw.decode(enc, "ignore")))
        except Exception:
            pass
    return _squeeze(max(cands, key=lambda s: len(CJK.findall(s)), default=""))


def read_pdf(path):
    """優先用 pymupdf(最快),退 pypdf,再退外部 pdftotext。"""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            return _squeeze("\n".join(p.get_text() for p in doc))
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return _squeeze("\n".join((p.extract_text() or "") for p in r.pages))
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"],
                             capture_output=True, timeout=120)
        return _squeeze(out.stdout.decode("utf-8", "ignore"))
    except Exception:
        return ""


def read_plain(path):
    raw = open(path, "rb").read()
    t = _decode_best(raw)
    if path.lower().endswith(".rtf"):
        t = re.sub(r"\\[a-z]+-?\d*\s?|[{}]", " ", t)
    return _squeeze(t)


def read_html(path):
    return _squeeze(_strip_tags(_decode_best(open(path, "rb").read())))


def extract(path):
    """回傳 (內文, 錯誤訊息)。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in EXT_OOXML:
            t = read_ooxml(path)
        elif ext in EXT_ODF:
            t = read_odf(path)
        elif ext in EXT_OLE2:
            t = read_ole2(path)
        elif ext in EXT_PDF:
            t = read_pdf(path)
        elif ext in EXT_HTML:
            t = read_html(path)
        elif ext in EXT_PLAIN:
            t = read_plain(path)
        else:
            return "", "unsupported"
        return t[:MAX_CHARS], ""
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- OCR

TESSDATA = _cfg_path("tessdata", os.path.join(DATA_DIR, "tessdata"))
def _find_tesseract():
    """
    各平台的 tesseract 位置不同,依序試:
    PATH → Windows 預設安裝路徑 → Homebrew(Apple Silicon / Intel)。
    """
    onpath = shutil.which("tesseract")
    if onpath:
        return onpath
    for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              "/opt/homebrew/bin/tesseract",      # Apple Silicon
              "/usr/local/bin/tesseract"):        # Intel Mac / Linux
        if os.path.exists(c):
            return c
    return "tesseract"       # 找不到就讓它報錯,訊息比猜路徑清楚


TESSEXE = _find_tesseract()
EXT_IMG = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
           ".heic", ".heif"}      # HEIC 需要 pillow-heif,見 _heif()

# 影片與音檔。抽不出內文,但「檔名和資料夾名稱」本身就是線索:
# 2024尾牙/開場.mp4、客戶會議/0312錄音.m4a —— 名字裡就寫明了是什麼。
EXT_MEDIA = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".mpg", ".mpeg",
             ".3gp", ".flv", ".webm", ".mts", ".m2ts", ".ts",
             ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma", ".amr"}

# 只收名字、不抽內文的檔案。圖片也在裡面:OCR 過的會被 ocr 指令換成
# 有內文的紀錄,沒 OCR 過的至少名字查得到。相簿動輒上萬張,不可能全部
# OCR;少了這一類,沒輪到的那幾千張連檔名都搜不到。
EXT_NAME = EXT_MEDIA | EXT_IMG

# OCR 範圍的「命名捷徑」,從 config.json 的 ocr_scopes 讀進來:
#
#   "ocr_scopes": {
#     "掃描件": ["掃描/2024", "掃描/2025"],
#     "截圖":   ["手機截圖"]
#   }
#
# 鍵名拿給 --scope 用,值是相對於 root 的目錄(可以給多個)。
# 不設也完全能用 —— --scope 直接給目錄路徑就好,這只是省得每次打長路徑。
# 寫死在程式裡的話,別人 clone 下來第一個指令就會找不到目錄。
SCOPES = {k: [_rel(*p.split("/")) for p in v]
          for k, v in (CONFIG.get("ocr_scopes") or {}).items()}

_tls = None   # 每個執行緒自己的 RapidOCR 實例(模型不可跨執行緒共用)
_cc = None    # OpenCC 簡→繁轉換器


def _to_trad(t):
    """
    RapidOCR 用的是 PP-OCRv4 簡體模型,辨識結果會夾雜簡體字
    (例:楊→杨、規則→规则、紙→纸)。不轉換的話查「楊」「規則」全都搜不到。
    用 OpenCC 的 s2tw 轉成台灣繁體用字。
    """
    global _cc
    if not t:
        return t
    if _cc is None:
        try:
            from opencc import OpenCC
            _cc = OpenCC("s2tw")
        except Exception:
            _cc = False          # 沒裝 opencc 就原樣保留,不讓 OCR 整批失敗
    return _cc.convert(t) if _cc else t


_OCR_THREADS = 4      # 每個 RapidOCR 實例可用的 onnxruntime 執行緒數(由 cmd_ocr 設定)
_MAXSIDE = 1600       # OCR 前把長邊縮到這個像素(由 cmd_ocr 設定)


def _rapid():
    global _tls
    import threading
    if _tls is None:
        _tls = threading.local()
    eng = getattr(_tls, "eng", None)
    if eng is None:
        from rapidocr_onnxruntime import RapidOCR
        # 不限制執行緒數的話,多個 worker 會各自吃滿 8 核心互相搶,反而更慢
        eng = _tls.eng = RapidOCR(intra_op_num_threads=_OCR_THREADS)
    return eng


_HEIF_TRIED = False


def _heif():
    """
    讓 PIL 認得 HEIC(iPhone 照片的預設格式)。只做一次。

    imgfind 匯入時也會註冊,但 findpile 只在以圖搜圖那幾個指令裡才匯入
    imgfind —— OCR 這條路徑不會經過它。少了這一行,.heic 進了 EXT_IMG
    卻會在 Image.open 當場失敗。
    """
    global _HEIF_TRIED
    if _HEIF_TRIED:
        return
    _HEIF_TRIED = True
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                               # noqa: BLE001
        pass          # 沒裝就讓 Image.open 自己報錯,訊息比這裡攔還清楚


def _prep(src):
    """
    掃描檔多是 A4 300dpi(2480x3507)。
    辨識前把長邊縮到 _MAXSIDE(預設 1600,約等於 140dpi)——
    對 12pt 以上的印刷字毫無影響,但速度差 2~4 倍。
    """
    import numpy as np
    _heif()
    from PIL import Image
    im = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src)
    im = im.convert("RGB")
    long_side = max(im.size)
    if long_side > _MAXSIDE:
        r = _MAXSIDE / long_side
        im = im.resize((max(int(im.width * r), 1), max(int(im.height * r), 1)),
                       Image.LANCZOS)
    return np.array(im)


def ocr_rapid(src):
    """src 可以是檔案路徑或 PNG bytes。回傳辨識出的文字(已轉繁體)。"""
    res, _ = _rapid()(_prep(src))
    if not res:
        return ""
    return _to_trad(_squeeze("\n".join(r[1] for r in res)))


def ocr_tess(src):
    import subprocess, tempfile
    env = dict(os.environ, TESSDATA_PREFIX=TESSDATA)
    tmp = None
    try:
        if isinstance(src, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(src)
            tmp.close()
            src = tmp.name
        r = subprocess.run([TESSEXE, src, "stdout", "-l", "chi_tra+eng", "--psm", "3"],
                           capture_output=True, env=env, timeout=180)
        return _squeeze(r.stdout.decode("utf-8", "ignore"))
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def ocr_one(src, engine):
    """
    engine:
      rapid — 只用 RapidOCR(快、對歪斜掃描準)
      tess  — 只用 Tesseract
      both  — RapidOCR 為主;辨識字數太少時改用 Tesseract,取較長者
    """
    if engine == "rapid":
        return ocr_rapid(src), "rapid"
    if engine == "tess":
        return ocr_tess(src), "tess"
    a = ocr_rapid(src)
    if len(a) >= 24:                 # RapidOCR 已經讀到足夠內容就不再跑 Tesseract
        return a, "rapid"
    b = ocr_tess(src)
    return (b, "tess") if len(b) > len(a) else (a, "rapid")


def ocr_file(path, engine, dpi, maxpages):
    """圖片直接辨識;PDF 先用 pymupdf 轉圖再逐頁辨識。"""
    if os.path.splitext(path)[1].lower() == ".pdf":
        import pymupdf
        pages, used = [], "rapid"
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc):
                if i >= maxpages:
                    break
                png = page.get_pixmap(dpi=dpi).tobytes("png")
                t, used = ocr_one(png, engine)
                if t:
                    pages.append(t)
        return "\n".join(pages), used
    return ocr_one(path, engine)


def cmd_ocr(args):
    global _OCR_THREADS, _MAXSIDE
    _OCR_THREADS, _MAXSIDE = args.threads, args.maxside
    con = db_open()
    cols = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    if "src" not in cols:
        con.execute("ALTER TABLE docs ADD COLUMN src TEXT DEFAULT 'extract'")
        con.commit()

    # ---- 組出待處理清單
    targets = []
    if args.scope == "pdf":
        targets = [r[0] for r in con.execute(
            "SELECT path FROM docs WHERE ext='.pdf' AND err='' AND nchars=0")]
    else:
        dirs = SCOPES.get(args.scope)
        roots = [os.path.join(args.root, d) for d in dirs] if dirs \
            else [args.scope if os.path.isabs(args.scope) else os.path.join(args.root, args.scope)]
        roots = guard_roots(roots)
        for r in roots:
            if not os.path.isdir(r):
                print("找不到目錄,略過:%s" % r)
                continue
            for dp, dn, fn in os.walk(r):
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                for f in fn:
                    if os.path.splitext(f)[1].lower() in EXT_IMG and not f.startswith("~$"):
                        targets.append(os.path.join(dp, f))

    # ---- 已做過且檔案沒變的就跳過(可中斷續跑)
    done = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT path, mtime, size FROM docs WHERE src LIKE 'ocr%'")}
    todo = []
    for p in targets:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_size == 0 or st.st_size > MAX_BYTES:
            continue
        old = done.get(p)
        if old and abs(old[0] - st.st_mtime) < 1 and old[1] == st.st_size and not args.redo:
            continue
        todo.append((p, st.st_mtime, st.st_size))

    print("範圍 %s:目標 %d 個,已完成 %d 個,本次要做 %d 個。"
          % (args.scope, len(targets), len(targets) - len(todo), len(todo)))
    if args.limit:
        todo = todo[:args.limit]
        print("--limit 生效,只做前 %d 個。" % len(todo))
    if not todo:
        print("沒有待處理項目。")
        return

    t0, n, nchars, fails = time.time(), [0], [0], []
    rows = []

    def work(item):
        p, mt, sz = item
        try:
            text, used = ocr_file(p, args.engine, args.dpi, args.maxpages)
            err = ""
        except Exception as e:
            text, used, err = "", args.engine, "%s: %s" % (type(e).__name__, e)
            fails.append((p, err))
        n[0] += 1
        nchars[0] += len(text)
        if n[0] % 25 == 0 or n[0] == len(todo):
            el = time.time() - t0
            rate = n[0] / max(el, .01)
            eta = (len(todo) - n[0]) / max(rate, .001)
            print("  %d/%d  %.1f 檔/秒  已辨識 %.1f 萬字  預估剩 %d 分"
                  % (n[0], len(todo), rate, nchars[0] / 10000, eta / 60), flush=True)
        return (p, mt, sz, os.path.splitext(p)[1].lower(), len(text), err, text, "ocr-" + used)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(work, todo):
            rows.append(r)
            if len(rows) >= 50:
                con.executemany(DOCS_INSERT, rows)
                con.commit()
                rows = []
    if rows:
        con.executemany(DOCS_INSERT, rows)
    con.commit()

    empty = sum(1 for r in rows if r[4] == 0)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("\nOCR 完成:%d 個檔案,耗時 %.1f 分,共辨識 %.1f 萬字。"
          % (len(todo), (time.time() - t0) / 60, nchars[0] / 10000))
    if fails:
        print("失敗 %d 個,前 3 個:" % len(fails))
        for p, e in fails[:3]:
            print("  %s\n    → %s" % (os.path.basename(p), e[:100]))
    print("索引檔現為 %.1f MB。現在可以直接用 find 查這些掃描檔了。"
          % (os.path.getsize(DB_PATH) / 1024 / 1024))


# ---------------------------------------------------------------- 索引

def db_open():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # 每次搜尋都要把整個內文欄掃一遍(中文沒有空白,FTS5 的 trigram
    # tokenizer 又只吃 3 字以上的詞,兩個字的關鍵字查出來是 0 筆,
    # 所以只能 LIKE 全表掃描)。改走 mmap 讓作業系統直接映射檔案,
    # 少掉一層複製 —— 240 MB 的索引實測 0.60 秒降到 0.46 秒。
    con.execute("PRAGMA mmap_size=1073741824")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS docs(
            path   TEXT PRIMARY KEY,
            mtime  REAL,
            size   INTEGER,
            ext    TEXT,
            nchars INTEGER,
            err    TEXT,
            text   TEXT,
            src    TEXT DEFAULT 'extract'   -- extract=直接抽文, ocr-rapid/ocr-tess=OCR 辨識
        );
        CREATE INDEX IF NOT EXISTS docs_ext ON docs(ext);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    # 舊版索引(無 src 欄)自動升級
    if "src" not in {r[1] for r in con.execute("PRAGMA table_info(docs)")}:
        con.execute("ALTER TABLE docs ADD COLUMN src TEXT DEFAULT 'extract'")
        con.commit()
    return con


DOCS_INSERT = ("INSERT OR REPLACE INTO docs"
               "(path,mtime,size,ext,nchars,err,text,src) VALUES (?,?,?,?,?,?,?,?)")


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)", (k, str(v)))
    con.commit()


def meta_get(con, k, default=None):
    try:
        row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    except sqlite3.OperationalError:          # 舊索引還沒有 meta 表
        return default
    return row[0] if row else default


def roots_get(con):
    """索引裡收錄過的所有根目錄。"""
    import json
    v = meta_get(con, "roots")
    if v:
        try:
            return json.loads(v)
        except Exception:
            pass
    one = meta_get(con, "root")          # 舊索引只記單一根目錄
    return [one] if one else []


def roots_add(con, root):
    import json
    rs = roots_get(con)
    # 新的根目錄若涵蓋了舊的(例如先只掃 ~/Word,後來改掃整個家目錄),
    # 把被涵蓋的那幾筆收起來,否則來源清單會愈積愈多,
    # 視窗狀態列會變成「來源 6 處:Word、lexipass、Documents…」。
    rs = [r for r in rs if not _under(r, root)]
    if not any(_sep_norm(r) == _sep_norm(root) for r in rs)        and not any(_under(root, r) for r in rs):   # 已被更大的根目錄涵蓋就不必登錄
        rs.append(root)
    meta_set(con, "roots", json.dumps(rs, ensure_ascii=False))
    meta_set(con, "root", rs[0])         # 保留舊欄位,相容舊版程式
    return rs


def findpile_is_index_only():
    """這台機器是否只有索引、沒有原始檔。"""
    return CONFIG.get("files_here") is False


def display_root(con):
    """顯示路徑時要去掉的前綴(單一,保留給舊呼叫處)。"""
    return (CONFIG.get("index_root")
            or meta_get(con, "root")
            or DEFAULT_ROOT)


def display_roots(con):
    r"""
    顯示路徑時要去掉的前綴,全部。
    索引可以同時收錄多個根目錄 —— 例如 Mac 上既有從 Windows
    複製過來的那份(D:/文件),也有這台自己的 ~/Documents。
    """
    rs = list(roots_get(con))
    ir = CONFIG.get("index_root")
    if ir and not any(r.lower() == ir.lower() for r in rs):
        rs.insert(0, ir)
    return rs or [DEFAULT_ROOT]


def walk(root, errors=None, cloud=None):
    """
    走訪 root 底下所有要抽內文的檔案。

    errors / cloud 是給 cmd_index 判斷「檔案真的不見了嗎」用的:

    errors  讀不到的目錄。os.walk 預設把 PermissionError 整個吞掉,
            macOS 使用者對「終端機想取用『文件』資料夾」按了不允許時,
            掃描會安安靜靜地得到零個檔案 —— 接著整個 root 底下的紀錄
            就被當成已刪除清掉了。

    cloud   iCloud「最佳化儲存空間」把未下載的檔案換成隱藏佔位檔
            .原檔名.icloud,原檔名在磁碟上並不存在。這些檔案還在雲端,
            不是被刪掉,所以要還原成原檔名記下來。
    """
    def _onerror(e):
        if errors is not None:
            errors.append(e)

    for dp, dn, fn in os.walk(root, onerror=_onerror):
        dn[:] = [d for d in dn
                 if d not in SKIP_DIRS
                 and not d.startswith("$")
                 and not d.startswith(".")                  # 隱藏資料夾一律跳過
                 and not d.lower().endswith(SKIP_SUFFIX)]   # macOS 套件
        if cloud is not None:
            for f in fn:
                if f.startswith(".") and f.endswith(".icloud"):
                    cloud.add(os.path.join(dp, f[1:-len(".icloud")]))
        for f in fn:
            if f.startswith("~$") or f.endswith(".tmp"):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXT_ALL and ext not in EXT_NAME:
                continue
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size == 0:
                continue
            # 大小上限只管要抽內文的檔。影片動輒一兩百 MB,拿這個上限
            # 去擋它們,等於又把整批影片排除在索引之外。
            if ext in EXT_ALL and st.st_size > MAX_BYTES:
                continue
            yield p, st.st_mtime, st.st_size


def index_targets(args):
    """這次要掃哪些資料夾。"""
    if getattr(args, "dir", None):
        return [os.path.abspath(os.path.expanduser(d)) for d in args.dir]
    if getattr(args, "mac", False):
        return [d for d in MAC_DIRS if os.path.isdir(d)]
    return [args.root]


def guard_roots(roots):
    r"""
    擋掉「掃整顆磁碟」,回傳過濾後的清單。

    DEFAULT_ROOT 是程式資料夾的上一層。這在「把程式放進資料堆裡面」的
    用法下剛好是對的,但別人 clone 下來放在 D:indpile,上一層就是
    D:\ —— 一個指令掃整顆硬碟,跑好幾小時,索引裡還全是系統檔。

    作者自己的機器上永遠不會發生,因為 config.json 設了 scan_root。
    這正是「自己用」和「給別人用」的差別,所以要擋在這裡。
    """
    ok = []
    for r in roots:
        a = os.path.abspath(r)
        if os.path.dirname(a) == a:          # 磁碟根目錄:D:\ 或 /
            print("拒絕掃描 %s —— 那是整顆磁碟的根目錄。" % a)
            print("  掃整顆硬碟要跑好幾小時,而且索引裡會塞滿系統檔。")
            print("  請指定要掃的資料夾,兩種做法任選:")
            print("    1. python findpile.py --root D:/你的文件 index")
            print("    2. 複製 config.example.json 成 config.json,設好 scan_root")
        else:
            ok.append(r)
    return ok


def is_borrowed_root(root):
    """
    這個根目錄是不是「索引是從別台複製來、原始檔不在這台」的那一份。
    在這種根目錄上執行 index 會掃不到任何檔案,然後把它底下的紀錄
    全部當成已刪除清掉。
    """
    if CONFIG.get("files_here") is not False:
        return False
    ir = CONFIG.get("index_root")
    if not ir:
        return False
    return (_sep_norm(root) == _sep_norm(ir)
            or _under(root, ir) or _under(ir, root))


def cmd_index(args):
    roots = guard_roots(index_targets(args))
    if not roots:
        return
    con = db_open()
    # 多帶一個 src:下面要靠它判斷「這張圖已經 OCR 過了」,
    # 不然 index 會把辨識好的內文覆蓋成空白的名稱紀錄。
    known = {r[0]: (r[1], r[2], r[3]) for r in
             con.execute("SELECT path, mtime, size, src FROM docs")}

    # 守門必須擋在 --full 前面。--full 的 DELETE 在迴圈外面執行,
    # 守門若放在迴圈裡,紀錄早就被刪掉了 —— 而且還會照常印出
    # 「真要重建請加 --force」,讓人以為守住了。
    if not args.force:
        keep = []
        for root in roots:
            if is_borrowed_root(root):
                n = sum(1 for p in known if _under(p, root))
                print("略過 %s" % root)
                print("  config.json 標示原始檔不在這台(files_here=false)。")
                print("  在這裡掃會掃不到東西,然後把索引裡的 %s 筆當成已刪除清掉。"
                      % format(n, ","))
                print("  這份索引直接用 find 查就好。真要重建請加 --force。")
            else:
                keep.append(root)
        roots = keep

    if args.full:
        # 只清掉這次要重建的根目錄,不要動索引裡其他來源的紀錄
        victims = [p for p in known if any(_under(p, r) for r in roots)]
        con.executemany("DELETE FROM docs WHERE path=?", [(p,) for p in victims])
        con.commit()
        for p in victims:
            known.pop(p, None)
        print("已清空 %s 筆舊紀錄,這些資料夾全部重建。" % format(len(victims), ","))

    found, todo, names, gone = set(), [], [], []
    for root in roots:
        if not os.path.isdir(root):
            print("找不到資料夾,略過:%s" % root)
            continue
        print("掃描:%s" % root)
        here, errs, cloud = set(), [], set()
        for p, mt, sz in walk(root, errs, cloud):
            here.add(p)
            o = known.get(p)
            if o is not None and abs(o[0] - mt) <= 1 and o[1] == sz:
                continue                         # 沒變,不用重做
            ext = os.path.splitext(p)[1].lower()
            if ext in EXT_ALL:
                todo.append((p, mt, sz))
            elif o is None or o[2] == "name":
                # 影片、音檔、還沒 OCR 的圖片:只記名字。
                # o[2] 不是 "name" 就跳過 —— 那是 OCR 辨識出來的內文,
                # 覆蓋掉等於把辨識結果丟了。
                names.append((p, mt, sz, ext, 0, "", None, "name"))

        if cloud:
            print("  %s 個檔案只存在 iCloud,本機沒有下載,這次抽不到內文。"
                  % format(len(cloud), ","))
            print("  (要收錄的話:關掉 系統設定 > Apple ID > iCloud >"
                  " 最佳化 Mac 儲存空間,等檔案下載完再掃一次)")

        if errs:
            # 讀取出錯時 here 一定不完整,這時絕對不能做「已刪除」判定。
            print("  !! 有 %d 個目錄讀不到(第一個:%s)" % (len(errs), errs[0]))
            print("  !! 這次跳過「已刪除」判定,以免把查得到的資料誤刪。")
            print("  !! macOS 請到 系統設定 > 隱私權與安全性 > 檔案與資料夾,"
                  "把「文件」「桌面」「下載」授權給終端機。")
        else:
            # 「消失的檔案」只在這個根目錄的範圍內判斷。不限定範圍的話,
            # 索引裡其他來源(例如從 Windows 複製過來的那一份)會因為
            # 在這台掃不到而被整批誤判成已刪除。
            # cloud 裡的檔案是「還在、只是沒下載」,不算消失。
            #
            # 最後那個 os.path.exists 是必要的,不是保險:
            # walk() 只走 EXT_ALL(文件檔),圖片根本不在裡面,所以 here
            # 永遠不含圖片路徑。而 ocr 指令寫進來的紀錄正好都是圖片 ——
            # 少了這個檢查,跑一次 index 就會把整批 OCR 成果當成「不見了」
            # 刪光。那可能是十幾個小時的辨識時間。
            #
            # 判定標準因此從「這次沒掃到」改成「磁碟上真的不存在」,
            # 這也讓刪除行為整體更保守。多出來的 stat 呼叫只發生在
            # 候選名單上,幾千筆不到一秒。
            gone += [p for p in known
                     if p not in here and p not in cloud and _under(p, root)
                     and not os.path.exists(p)]
        found |= here
        roots_add(con, root)

    meta_set(con, "built_on", sys.platform)

    if gone:
        con.executemany("DELETE FROM docs WHERE path=?", [(p,) for p in gone])
        con.commit()

    if names:
        con.executemany(DOCS_INSERT, names)
        con.commit()

    print("掃到 %d 個檔案,其中 %d 個需要抽內文,%d 個只記名稱,%d 個已移除。"
          % (len(found), len(todo), len(names), len(gone)))
    if not todo:
        print("索引已是最新。")
        return

    t0 = time.time()
    done = [0]
    rows = []

    def work(item):
        p, mt, sz = item
        text, err = extract(p)
        done[0] += 1
        if done[0] % 200 == 0:
            el = time.time() - t0
            print("  %d/%d  (%.0f 檔/秒)" % (done[0], len(todo), done[0] / max(el, .01)),
                  flush=True)
        return (p, mt, sz, os.path.splitext(p)[1].lower(), len(text), err, text, "extract")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(work, todo):
            rows.append(r)
            if len(rows) >= 300:
                con.executemany(DOCS_INSERT, rows)
                con.commit()
                rows = []
    if rows:
        con.executemany(DOCS_INSERT, rows)
    con.commit()

    bad = con.execute("SELECT COUNT(*) FROM docs WHERE err<>''").fetchone()[0]
    # src='name' 的紀錄本來就沒有內文,不是「抽到空白」
    empty = con.execute("SELECT COUNT(*) FROM docs"
                        " WHERE err='' AND nchars=0 AND src<>'name'").fetchone()[0]
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # 先落盤再量大小
    mb = os.path.getsize(DB_PATH) / 1024 / 1024
    print("完成,耗時 %.1f 秒。索引檔 %.1f MB。抽取失敗 %d 個、抽到空白 %d 個"
          "(多為掃描影像 PDF,需 OCR;用 stats 看明細)。"
          % (time.time() - t0, mb, bad, empty))


# ---------------------------------------------------------------- 搜尋

def snippets(text, kws, want, width):
    """取關鍵字前後文,去重後回傳。"""
    out, seen = [], set()
    marks = sorted(m.start() for kw in kws for m in re.finditer(re.escape(kw), text))
    for i in marks:
        seg = _squeeze(text[max(0, i - width):i + width]).replace("\n", " ")
        key = re.sub(r"\s", "", seg)[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(seg)
        if len(out) >= want:
            break
    return out


def _bigrams(k):
    """會議紀錄 → ['會議','議紀','紀錄'](重疊二字組)"""
    return [k[i:i + 2] for i in range(len(k) - 1)]


def _loose_count(text, kw, need=0.7):
    """
    模糊比對:OCR 文字只要錯一個字,長關鍵字就整個比對不到
    (「會議紀錄」4 個字全對才算命中)。
    改成把關鍵字切成重疊二字組,只要有 need 比例以上出現就算命中,
    命中次數取各二字組出現次數的中位數。
    """
    bg = _bigrams(kw)
    if len(bg) < 2:
        return text.count(kw)
    cs = [text.count(b) for b in bg]
    hit = sum(1 for c in cs if c)
    if hit / len(bg) < need:
        return 0
    nz = sorted(c for c in cs if c)
    return nz[len(nz) // 2]


def search(kws, mode="any", con=None, loose=False):
    """
    搜尋核心。回傳 (結果清單, 耗時秒數)。
    清單每筆是 dict,照分數排序。CLI 和視窗版共用這個函式。

    loose=True 時改用模糊比對,適合搜尋 OCR 辨識的掃描檔。
    """
    con = con or db_open()
    t0 = time.time()

    if loose:
        # 先用「第一個二字組」粗篩,再逐檔做模糊計分
        probes = [_bigrams(k)[0] if len(k) > 2 else k for k in kws]
        joiner = " AND " if mode == "all" else " OR "
        where = joiner.join(["text LIKE ?"] * len(probes))
        params = ["%%%s%%" % p for p in probes]
    else:
        joiner = " AND " if mode == "all" else " OR "
        where = joiner.join(["text LIKE ?"] * len(kws))
        params = ["%%%s%%" % k for k in kws]

    # 內文命中:text 一起帶回來。分兩趟(先拿 path 再逐筆 point lookup)
    # 等於把整個內文欄讀兩遍,常見字實測慢 0.3 秒。
    hit = {}
    for p, text, nchars, src, size in con.execute(
            "SELECT path, text, nchars, src, size FROM docs"
            " WHERE nchars>0 AND (%s)" % where, params):
        hit[p] = (text or "", nchars or 0, src or "extract", size or 0)

    # 路徑也算命中,而且是整個路徑不只檔名(查「報價」要讓
    # 「報價單-範本.xlsx」浮上來,查「2024尾牙」要讓那個資料夾底下的
    # 照片和影片都找得到)。資料夾名稱的資訊量很高,只比對 basename
    # 等於把一半的線索丟掉。順帶一提 `k in p` 比
    # `k in os.path.basename(p)` 快 50 倍:後者對每一列都要呼叫一次函式。
    for p, nchars, src, size in con.execute(
            "SELECT path, nchars, src, size FROM docs"):
        if p in hit or not any(k in p for k in kws):
            continue
        if nchars:
            # 有內文、只是內文沒命中。前後文面板還是要有東西可顯示,
            # 所以這一類才去取 text —— 數量少,不會變成全表再讀一遍。
            row = con.execute("SELECT text FROM docs WHERE path=?", (p,)).fetchone()
            hit[p] = (row[0] or "" if row else "", nchars, src or "extract", size or 0)
        else:
            hit[p] = ("", 0, src or "extract", size or 0)

    out = []
    for p, (text, nchars, src, size) in hit.items():
        counts = {k: (_loose_count(text, k) if loose else text.count(k)) for k in kws}
        total = sum(counts.values())
        base = os.path.basename(p)
        nmatch = sum(1 for k in kws if k in base)
        # 資料夾名稱命中的權重刻意壓低。查一個資料夾名會打到它底下
        # 幾千張照片,給它跟檔名一樣的 3.0 分,真正的文件會被全部壓下去。
        dmatch = sum(1 for k in kws if k not in base and k in os.path.dirname(p))
        if mode == "all" and not all(counts[k] or k in p for k in kws):
            continue
        if total == 0 and nmatch == 0 and dmatch == 0:
            continue
        kb = max(nchars / 1024, 1.0)
        out.append({
            "score": total / (kb ** 0.75) + nmatch * 3.0 + dmatch * 0.5,
            "total": total, "counts": counts, "namehit": nmatch,
            "dirhit": dmatch, "kb": kb, "path": p, "text": text, "src": src,
            "size": size, "nameonly": src == "name",
        })
    # 有內文的排前面,只有名字的排後面 —— 這兩種結果的性質不一樣,
    # 混在一起排名的話,沒內文的紀錄會把文件稀釋掉。
    out.sort(reverse=True,
             key=lambda r: (0 if r["nameonly"] else 1, r["score"], r["total"]))
    return out, time.time() - t0


def cmd_find(args):
    kws = args.keywords
    con = db_open()        # 下面要問索引是在哪個根目錄建的
    res, el = search(kws, args.mode, con, loose=args.loose)
    # 「有內文」和「只有名字」分兩區。影片、音檔、沒 OCR 的圖片抽不出
    # 內文,拿來跟文件一起排名只會互相干擾。
    docs = [r for r in res if not r["nameonly"]]
    named = [r for r in res if r["nameonly"]]
    # 攤成舊的 tuple 形式,下面產生報告的程式碼不必改
    scored = [(r["score"], r["total"], r["counts"], r["namehit"],
               r["kb"], r["path"], r["text"]) for r in docs]
    top = scored[:args.top]

    head = ("# 搜尋:%s\n\n關鍵字 `%s`(%s)　內文或檔名命中 %d 個檔案,列出前 %d 名　查詢耗時 %.2f 秒\n"
            % ("、".join(kws), "` `".join(kws),
               "必須全部出現" if args.mode == "all" else "任一出現",
               len(scored), len(top), el))
    lines = [head,
             "> 分數 = 命中次數 ÷ 檔案文字量^0.75(＋檔名命中加成)。"
             "分數高＝該關鍵字是這個檔案的主題,而非一本大部頭裡剛好出現的一個詞。\n",
             "| # | 分數 | 命中 | 文字量 | 檔案 |",
             "| --- | --- | --- | --- | --- |"]
    droots = display_roots(con)
    for i, (score, total, counts, nmatch, kb, p, _t) in enumerate(top, 1):
        rel = strip_root_any(p, droots)
        tag = " 📌檔名" if nmatch else ""
        detail = total if len(kws) == 1 else "%d (%s)" % (
            total, " ".join("%s:%d" % (k, c) for k, c in counts.items() if c))
        lines.append("| %d | %.2f | %s%s | %.0fK | `%s` |" % (i, score, detail, tag, kb, rel))

    if named:
        lines.append("\n---\n\n## 只有名稱命中的檔案(%d 個)\n" % len(named))
        lines.append("> 影片、音檔、還沒 OCR 的圖片抽不出內文,只能靠檔名和"
                     "資料夾名稱找。這一區不計分,照路徑排。\n")
        lines.append("| # | 類型 | 命中位置 | 大小 | 檔案 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for i, r in enumerate(sorted(named, key=lambda x: x["path"])[:args.top], 1):
            rel = strip_root_any(r["path"], droots)
            where = "📌 檔名" if r["namehit"] else "📁 資料夾"
            lines.append("| %d | %s | %s | %s | `%s` |"
                         % (i, os.path.splitext(r["path"])[1].lstrip(".").upper(),
                            where, fmt_size(r["size"]), rel))

    if args.ctx > 0:
        lines.append("\n---\n\n## 前後文摘要\n")
        for i, (score, total, counts, nmatch, kb, p, text) in enumerate(top, 1):
            rel = strip_root_any(p, droots)
            lines.append("### %d. %s\n" % (i, split_display(rel)[1]))
            lines.append("`%s`　命中 %d 次 / %.0fK 字\n" % (rel, total, kb))
            segs = snippets(text, kws, args.ctx, args.width)
            if segs:
                for s in segs:
                    lines.append("- …%s…\n" % s)
            else:
                lines.append("- (僅檔名命中,內文無此關鍵字)\n")

    report = "\n".join(lines)

    # 終端機只印表格,前後文留在報告檔裡
    print("\n".join(lines[:4 + len(top)]))
    print()
    if named:
        # 名稱區留在報告檔裡,但終端機至少要講一聲有這一區,
        # 不然影片和沒 OCR 的圖片等於查到了也沒人知道。
        kinds = {}
        for r in named:
            e = os.path.splitext(r["path"])[1].lstrip(".").upper()
            kinds[e] = kinds.get(e, 0) + 1
        top3 = sorted(kinds.items(), key=lambda kv: -kv[1])[:3]
        print("另有 %d 個檔案只有名稱命中(%s)—— 影片、音檔、還沒 OCR 的圖片,"
              % (len(named), "、".join("%s %d" % kv for kv in top3)))
        print("明細在報告檔的「只有名稱命中的檔案」那一節。")
        print()

    if not args.no_report:
        os.makedirs(REPORT_DIR, exist_ok=True)
        name = re.sub(r'[\\/:*?"<>|]', "_", "_".join(kws))[:60]
        out = args.out or os.path.join(
            REPORT_DIR, "%s_%s.md" % (name, time.strftime("%Y%m%d_%H%M")))
        io.open(out, "w", encoding="utf-8").write(report)
        print("報告已存:%s" % out)
        if args.open:
            try:
                open_path(out)
            except Exception as e:
                print("(無法自動開啟:%s)" % e)


# ---------------------------------------------------------------- 打包

def cmd_pack(args):
    """
    把「搬到另一台電腦」需要的東西複製成一個資料夾。

    索引是 SQLite + WAL 三個檔案,直接複製 index.db 會漏掉還沒落盤的
    WAL 內容,所以這裡用 VACUUM INTO 輸出一個乾淨的單一檔案。
    """
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    con = db_open()
    root = meta_get(con, "root", DEFAULT_ROOT)
    n, ch = con.execute("SELECT COUNT(*), COALESCE(SUM(nchars),0) FROM docs").fetchone()
    ocr = con.execute("SELECT COUNT(*) FROM docs WHERE src LIKE 'ocr%'").fetchone()[0]

    print("正在整理索引(VACUUM INTO,會比原檔小一些)…")
    dst_db = os.path.join(out, "index.db")
    if os.path.exists(dst_db):
        os.remove(dst_db)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("VACUUM INTO ?", (dst_db,))
    con.close()

    code = ["findpile.py", "gui.py", "README.md", "config.example.json",
            # 新介面。另一台不一定裝了 PySide6,所以 gui.py 還是要帶,
            # 而且 搜尋視窗_新介面.bat 偵測不到 Qt 時會自己退回去開它。
            "app.py", "test_app.py", "搜尋視窗_新介面.bat",
            # gui.py 會 import 它。漏掉的話另一台按「以圖搜圖」會說缺 opencv,
            # 其實是根本沒這個檔 —— 錯誤訊息會把人帶往錯的方向。
            "imgfind.py",
            "搜尋視窗.bat", "查.bat", "OCR全跑.bat",
            # 三個 .command 都 source 它,漏掉的話 macOS 啟動器全部失效
            "_pick_python.sh",
            "開啟搜尋.command", "安裝.command", "建立Mac索引.command",
            # Mac 的新介面啟動器。它 source _pick_python.sh 取 pick_python_qt,
            # 所以這兩個檔要嘛一起帶、要嘛一起不帶。
            "開啟搜尋_新介面.command",
            "安裝Python.command"]
    for f in code:
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, f))

    # 圖片特徵庫(imgfeat.db)刻意不打包:它三百多 MB,而且裡面記的是
    # 這台的圖片路徑,搬到另一台也對不上。那台要用以圖搜圖,自己跑
    # 一次 imgindex 比較快(約 15 分鐘),不必搬。

    # 預設的 config.json:只有索引、沒有原始檔的情形
    import json
    cfg = {
        "index_root": root,
        "path_map": {},
        "files_here": False,
        "_說明": ("files_here=false 表示這台只有索引、沒有原始檔,"
                  "按『開啟檔案』會提示並複製路徑。日後若把資料也複製過來,"
                  "把 path_map 填成 {\"%s\": \"你的新路徑\"} 即可。" % root),
    }
    io.open(os.path.join(out, "config.json"), "w", encoding="utf-8").write(
        json.dumps(cfg, ensure_ascii=False, indent=2))

    mb = os.path.getsize(dst_db) / 1024 / 1024
    total = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out)) / 1024 / 1024
    print()
    print("打包完成:%s" % out)
    print("  索引      %.1f MB(%s 個檔案、%.0f 萬字,其中 %s 個 OCR)"
          % (mb, format(n, ","), ch / 10000, format(ocr, ",")))
    print("  整包合計  %.1f MB" % total)
    print()
    print("把整個資料夾複製到另一台電腦,然後:")
    print("  macOS    雙擊「安裝.command」→ 再雙擊「開啟搜尋_新介面.command」")
    print("  Windows  雙擊「搜尋視窗_新介面.bat」")
    print("           (沒有 PySide6 時兩邊都會自動退回舊介面)")


# ---------------------------------------------------------------- 合併

def cmd_merge(args):
    """
    把另一個 index.db 的內容併進目前這個索引。

    用途:Mac 上先建好本機檔案的索引,之後再把 Windows 那份
    (pack 產生的)合併進來 —— 兩邊的先後順序就不重要了。

    docs 的主鍵是完整路徑,所以兩份索引的檔案不會互相覆蓋;
    真的同路徑同檔案時以「被合併進來的那份」為準。
    """
    src = os.path.abspath(os.path.expanduser(args.db))
    if not os.path.exists(src):
        print("找不到檔案:%s" % src)
        return
    if os.path.abspath(src) == os.path.abspath(DB_PATH):
        print("來源和目前的索引是同一個檔案,不用合併。")
        return

    con = db_open()
    before = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    con.execute("ATTACH DATABASE ? AS src", (src,))
    try:
        # 來源可能根本不是 findpile 索引,也可能是沒有 src 欄的舊版。
        # db_open() 的自動升級只升級 main,不會動到 ATTACH 進來的 src。
        scols = {r[1] for r in con.execute("PRAGMA src.table_info(docs)")}
        if not scols:
            print("這不是 findpile 索引(裡面沒有 docs 表):%s" % src)
            return
        add = con.execute("SELECT COUNT(*) FROM src.docs").fetchone()[0]
        print("目前索引 %s 筆,來源 %s 筆,合併中…"
              % (format(before, ","), format(add, ",")))
        sel_src = "src" if "src" in scols else "'extract'"
        con.execute("INSERT OR REPLACE INTO docs"
                    "(path,mtime,size,ext,nchars,err,text,src) "
                    "SELECT path,mtime,size,ext,nchars,err,text,%s FROM src.docs"
                    % sel_src)

        # 來源的根目錄清單也要併過來,否則顯示路徑時去不掉前綴
        # 來源可能是舊格式,只記了單數的 root。查不到 roots 時是回傳
        # None 而不是丟例外,所以要明確判斷、不能只靠 try/except。
        import json
        other = []
        try:
            row = con.execute("SELECT v FROM src.meta WHERE k='roots'").fetchone()
            if row and row[0]:
                v = json.loads(row[0])
                # 一定要檢查型別。若 roots 存成 JSON 字串而不是陣列,
                # 下面的 for r in other 會逐「字元」呼叫 roots_add,
                # 把來源根目錄拆成 ["D", ":", "/", ...] 汙染整份清單。
                if isinstance(v, list) and all(isinstance(r, str) for r in v):
                    other = v
                else:
                    print("  來源的 roots 格式不對,改用舊的 root 欄位。")
            if not other:
                row = con.execute("SELECT v FROM src.meta WHERE k='root'").fetchone()
                if row and row[0]:
                    other = [row[0]]
        except sqlite3.OperationalError:
            # 更舊的索引根本沒有 meta 表 —— 不能默默跳過,
            # 不然來源的根目錄沒人知道,顯示路徑就不會縮短。
            print("  來源沒有 meta 表,查不到它的根目錄。")
            print("  請在 config.json 的 index_root 手動補上,否則顯示路徑不會縮短。")
        con.commit()
    finally:
        con.execute("DETACH DATABASE src")

    for r in other:
        roots_add(con, r)

    after = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("完成:%s 筆 → %s 筆(新增 %s 筆)"
          % (format(before, ","), format(after, ","), format(after - before, ",")))
    print("收錄的來源:")
    for r in display_roots(con):
        print("   %s" % r)
    print()
    print("如果來源的原始檔不在這台,記得在 config.json 設 \"files_here\": false,")
    print("這樣 index 才不會把那些紀錄當成已刪除清掉。")


# ---------------------------------------------------------------- 狀態

def cmd_stats(args):
    con = db_open()
    n, chars = con.execute("SELECT COUNT(*), COALESCE(SUM(nchars),0) FROM docs").fetchone()
    print("索引檔    : %s (%.1f MB)" % (DB_PATH, os.path.getsize(DB_PATH) / 1024 / 1024))
    print("已索引檔案: %d 個,共 %.1f 萬字" % (n, chars / 10000))
    print("\n依類型:")
    for ext, c, ch in con.execute(
            "SELECT ext, COUNT(*), COALESCE(SUM(nchars),0) FROM docs "
            "GROUP BY ext ORDER BY COUNT(*) DESC LIMIT 15"):
        print("  %-7s %5d 檔  %8.0f 萬字" % (ext, c, ch / 10000))
    try:
        import imgfind
        ist = imgfind.stats(DATA_DIR)
    except Exception:
        ist = None
    print("")
    if ist is None:
        print("圖片特徵  : 還沒建立(以圖搜圖需要:python findpile.py imgindex)")
    else:
        print("圖片特徵  : %d 張可比對 / 共 %d 張,%.0f MB"
              % (ist["ok"], ist["total"], ist["db_mb"]))

    bad = con.execute(
        "SELECT ext, COUNT(*) FROM docs WHERE err<>'' GROUP BY ext ORDER BY 2 DESC").fetchall()
    if bad:
        print("\n抽取失敗:")
        for ext, c in bad:
            print("  %-7s %d 檔" % (ext, c))
        print("  (範例)")
        for p, e in con.execute("SELECT path, err FROM docs WHERE err<>'' LIMIT 5"):
            print("    %s\n      → %s" % (os.path.basename(p), e[:90]))


# ------------------------------------------------------- 以圖搜圖(imgfind)

def _img_progress(done, total, el, left):
    sys.stdout.write("\r  %d/%d  已 %.0f 秒,約剩 %.0f 秒   "
                     % (done, total, el, left))
    sys.stdout.flush()


def cmd_imgindex(args):
    """建立圖片特徵。跟文字索引完全分開,存成另一個 imgfeat.db。"""
    import imgfind

    roots = [args.root] if args.dir is None else [os.path.abspath(d) for d in args.dir]
    print("建立圖片特徵")
    print("  掃描範圍:%s" % "、".join(roots))
    print("  特徵檔  :%s" % imgfind.feat_db_path(DATA_DIR))
    if not imgfind.HAS_HEIC:
        print("  註:沒有 pillow-heif,.heic(iPhone 照片)會被跳過。")
        print("     要支援請執行:pip install pillow-heif")

    st = imgfind.build(roots, DATA_DIR, redo=args.redo, limit=args.limit,
                       progress=_img_progress)
    print("")
    print("  找到圖片 %d 張,這次需要處理 %d 張" % (st["found"], st["todo"]))
    if st["todo"]:
        print("  完成 %d 張,耗時 %.1f 分鐘"
              % (st["done"], st.get("elapsed", 0) / 60))
        if st.get("too_small"):
            print("  跳過 %d 張太小的(App 圖示、佔位檔,短邊不足 %d 像素)"
                  % (st["too_small"], imgfind.MIN_SIDE))
        if st["failed"]:
            print("  讀不到 %d 張" % st["failed"])
    if st.get("heic_skipped"):
        print("  其中 %d 張是 HEIC,因為缺 pillow-heif 被跳過" % st["heic_skipped"])
    if st.get("removed"):
        print("  清掉已不存在的 %d 筆" % st["removed"])
    s = imgfind.stats(DATA_DIR)
    if s:
        print("  目前特徵庫:%d 張(可比對 %d 張),%.0f MB"
              % (s["total"], s["ok"], s["db_mb"]))


def cmd_findimg(args):
    """拿一張照片去比對檔案堆裡的圖。"""
    import imgfind

    if not os.path.exists(args.image):
        print("找不到這個檔案:%s" % args.image)
        return
    if imgfind.stats(DATA_DIR) is None:
        print("還沒建立圖片特徵。請先執行:")
        print("")
        print("    python findpile.py imgindex")
        print("")
        return

    print("查詢圖:%s" % args.image)
    if args.deep:
        print("深度搜尋(ORB + 幾何驗證),約需 40 秒…")
    try:
        rows, note = imgfind.search(args.image, DATA_DIR, deep=args.deep,
                                    top=args.top, progress=_img_progress,
                                    rough_desc=None if args.full
                                    else imgfind.ROUGH_DESC)
    except RuntimeError as e:
        print("")
        print("%s" % e)
        return
    print("")
    print(note)
    if not rows:
        print("")
        print("沒有相似的。可以試試 --deep —— 慢一點,但認得出歪斜、裁切過的。")
        return

    con = db_open()
    droots = display_roots(con)
    con.close()
    print("")
    print("  #   相似度  判定方式   吻合度    檔案")
    print("  " + "-" * 76)
    for i, r in enumerate(rows, 1):
        print("  %2d  %5.1f%%  %-9s %-8s %s %s"
              % (i, r["score"], r["how"], r["detail"],
                 "✓" if r["sure"] else "?",
                 strip_root_any(r["path"], droots)))
    print("")
    print("  ✓ = 確定相符(通過幾何驗證)")
    print("  ? = 只是版面接近,沒通過驗證 —— 請自己開起來看一眼")


# ---------------------------------------------------------------- CLI

def main():
    _console()
    ap = argparse.ArgumentParser(
        prog="findpile",
        description="本機檔案關鍵字搜尋(離線,內容不離開這台電腦)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="要掃的根目錄(預設:本工具的上層資料夾)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="建立/更新索引")
    pi.add_argument("--full", action="store_true", help="清空重建")
    pi.add_argument("--workers", type=int, default=8, help="並行數(預設 8)")
    pi.add_argument("--dir", action="append", metavar="資料夾",
                    help="要加入索引的資料夾,可重複指定。"
                         "不給就用 --root(預設是本工具的上層資料夾)")
    pi.add_argument("--mac", action="store_true",
                    help="macOS 常用範圍:文件、桌面、下載、iCloud 雲碟")
    pi.add_argument("--force", action="store_true",
                    help="即使 config.json 標示這台沒有原始檔也照樣建索引"
                         "(會清掉索引裡所有找不到的檔案,請確定你要這樣做)")
    pi.set_defaults(func=cmd_index)

    pf = sub.add_parser("find", help="查關鍵字")
    pf.add_argument("keywords", nargs="+", help="一個或多個關鍵字")
    pf.add_argument("--mode", choices=["any", "all"], default="any",
                    help="any=任一出現(預設);all=必須全部出現")
    pf.add_argument("--top", type=int, default=40, help="列出前幾名(預設 40)")
    pf.add_argument("--ctx", type=int, default=3, help="每檔幾段前後文(預設 3,0=不要)")
    pf.add_argument("--width", type=int, default=180, help="前後文字數(單邊,預設 180)")
    pf.add_argument("--out", help="報告輸出路徑")
    pf.add_argument("--no-report", action="store_true", help="不產生報告檔")
    pf.add_argument("--open", action="store_true", help="產生後自動開啟報告")
    pf.add_argument("--loose", action="store_true",
                    help="模糊比對:把長關鍵字切成二字組,7成以上出現就算命中。"
                         "OCR 掃描檔只要錯一個字,長關鍵字就整個比對不到,查掃描檔時建議開啟")
    pf.set_defaults(func=cmd_find)

    po = sub.add_parser("ocr", help="對掃描檔／圖片做 OCR,結果併入索引")
    po.add_argument("--scope", default=".",
                    help="要辨識的目錄(相對 root,或絕對路徑)。也可以用 "
                         "config.json 的 ocr_scopes 取好的名字,或 pdf"
                         "(= 抽不出文字的 PDF)。預設 . 是整個 root")
    po.add_argument("--engine", choices=["rapid", "tess", "both"], default="both",
                    help="rapid=RapidOCR;tess=Tesseract;both=RapidOCR為主、字太少時改用Tesseract(預設)")
    po.add_argument("--workers", type=int, default=4, help="並行數(預設 4,OCR 吃 CPU)")
    po.add_argument("--limit", type=int, help="只做前 N 個(試跑用)")
    po.add_argument("--dpi", type=int, default=220, help="PDF 轉圖解析度(預設 220)")
    po.add_argument("--maxpages", type=int, default=40, help="每個 PDF 最多辨識幾頁(預設 40)")
    po.add_argument("--threads", type=int, default=3,
                    help="每個 worker 可用的 CPU 執行緒(預設 3;workers×threads 建議≤核心數-2,"
                         "這樣 OCR 跑的時候電腦還能正常用)")
    po.add_argument("--maxside", type=int, default=1600,
                    help="辨識前把圖片長邊縮到幾像素(預設 1600,約 140dpi;調小更快但可能漏字)")
    po.add_argument("--redo", action="store_true", help="已做過的也重做")
    po.set_defaults(func=cmd_ocr)

    pp = sub.add_parser("pack", help="打包成可複製到另一台電腦的資料夾")
    # 2026-10-25 起改成輸出到程式資料夾底下的「攜帶版」。
    # 以前放在外面是怕 index 把攜帶版裡的 README.md 也索引進來變成
    # 重複結果 —— 但掃描範圍是 scan_root,而程式資料夾
    # 不在裡面,這個顧慮已經不存在。東西收在一起比較好找。
    pp.add_argument("--out",
                    default=os.path.join(HERE, "攜帶版"),
                    help="輸出資料夾(預設:程式資料夾底下的「攜帶版」)")
    pp.set_defaults(func=cmd_pack)

    pm = sub.add_parser("merge", help="把另一個 index.db 併進目前的索引")
    pm.add_argument("db", help="要併進來的 index.db 路徑")
    pm.set_defaults(func=cmd_merge)

    pii = sub.add_parser("imgindex", help="建立圖片特徵(以圖搜圖用)")
    pii.add_argument("--dir", action="append", metavar="資料夾",
                     help="要建特徵的資料夾,可重複指定;不給就用 --root")
    pii.add_argument("--redo", action="store_true", help="已建好的也重算")
    pii.add_argument("--limit", type=int, help="只做前 N 張(試跑用)")
    pii.set_defaults(func=cmd_imgindex)

    pfi = sub.add_parser("findimg", help="拿一張照片找相同／相似的圖")
    pfi.add_argument("image", help="查詢用的照片路徑")
    pfi.add_argument("--deep", action="store_true",
                     help="直接跑深度搜尋(ORB+幾何驗證)。慢約 40 秒,"
                          "但旋轉、裁切、手持拍攝的歪斜都認得")
    pfi.add_argument("--full", action="store_true",
                     help="深度搜尋的第一關用全部 600 個描述子(預設 300)。"
                          "慢一倍,實測不會更準 —— 300 和 600 的排名結果一樣。"
                          "兩種設定都會掃完整個索引")
    pfi.add_argument("--top", type=int, default=40, help="列出前幾名(預設 40)")
    pfi.set_defaults(func=cmd_findimg)

    ps = sub.add_parser("stats", help="索引狀態")
    ps.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.root = os.path.abspath(args.root)
    args.func(args)


if __name__ == "__main__":
    main()
