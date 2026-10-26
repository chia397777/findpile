#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
imgfind.py —— 以圖搜圖:拍一張,把檔案堆裡相同或相似的圖撈出來

跟 findpile 的關鍵字搜尋是兩套獨立的東西,互不干擾:
  文字  ->  findpile.py  關鍵字 + 密度排名
  圖片  ->  imgfind.py  影像特徵比對

完全離線、不用模型、不上傳任何東西。用的是兩個傳統影像處理手法,
各補對方的死穴 —— 下面每個數字都是在這個庫的真實檔案上量出來的:

  pHash(感知雜湊)   建特徵 0.8 ms/張、8 bytes/張
      ✅ 縮放、重壓 JPEG、調亮、加雜訊 —— 距離全都 0~2
      ❌ 旋轉超過 5 度、裁切超過 10% 就失效
      ⚠️ 不能自己下判斷:就算門檻收到 2,全庫 13,570 張還是平均
         每次誤判 0.4 張 —— 掃描文件在 8×8 DCT 解析度下本來就幾乎
         一樣(大片白、同樣信箋頭、同樣表格)。它只配當粗篩。

  ORB + RANSAC       建特徵 10 ms/張、23 KB/張
      ✅ 旋轉 90 度、裁切 30%、手持拍紙本(歪斜+裁切+亮度不均)都抓到
      ⚠️ 只算「配對數」的話誤判 67%,過了 RANSAC 幾何驗證降到 0.51%
      ⚠️ RANSAC 很貴:配對 3.4 ms/張,加上 RANSAC 變成 50 ms/張 ——
         是 15 倍,不是 1.5 倍。所以它只能用在少量候選上。

查詢分三關,愈往後愈準也愈貴,前面過得去就不往下走:

  1. pHash 粗篩          0.3 秒   13,388 張排序,留距離 14 以內的
  2. 驗證那幾十張候選    約 8 秒   判定 ✓。同一張圖(縮放/重壓/亮度)
                                  在這裡就結束
  3. 深度搜尋(後盾)    約 34 秒   前兩關空手才跑,掃完整個索引。
                                  自己也分兩段:全庫先算配對數(查詢端
                                  只用 300 個描述子,22 秒),再對配對數
                                  最高的 120 張跑 RANSAC(6 秒)

⚠️ 第 3 關刻意**不用 pHash 預篩**。曾經為了省時間只掃 pHash 前 3,000 名,
   召回率掉到 7/10 —— 失敗的三張 pHash 排第 4,960 / 6,602 / 3,835 名,
   可是 RANSAC inlier 有 117 / 241 / 44(✓ 只要 25)。不是認不出來,
   是沒被放進來比。pHash 距離對重度變形的圖沒有鑑別力。

用法:
    python findpile.py imgindex              建立圖片特徵(一次性,約 12 分鐘)
    python findpile.py findimg 照片.jpg       搜尋
    python findpile.py findimg 照片.jpg --deep  跳過前兩關,直接深度搜尋
    python findpile.py findimg 照片.jpg --full  第一關用全部 600 個描述子
"""

import os
import sqlite3
import sys
import time

import numpy as np

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

# HEIC 是 iPhone 照片的預設格式,cv2 和 PIL 本身都讀不了,要外掛。
# 沒裝不會壞,只是 .heic 會被跳過並在最後統計裡說明。
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIC = True
except Exception:
    HAS_HEIC = False

EXT_IMG = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
EXT_HEIC = {".heic", ".heif"}

# ORB 特徵點數。600 是實測過的:裁掉 30% 還留得住 14 個 inlier。
# 調低省空間但抗裁切會變差(每張約 30 bytes × N)。
ORB_FEATURES = 600

# 短邊低於這個像素就整張跳過。
# 專案資料夾裡混著一堆 App 圖示素材,其中有 1×1 的佔位 PNG
# (phone_sentinel\ios\...\LaunchImage.png)。ORB 會對輸入建影像金字塔,
# 1×1 縮到下一層就變 0×0,OpenCV 直接丟 assertion 把整個建立流程打斷。
# 而且這種圖示本來就不是要搜的東西 —— 1×1 放大成 32×32 是一片純色,
# 算出來的 pHash 會跟所有純色圖互相匹配,只會製造假命中。
MIN_SIDE = 32

# 比對門檻,全部來自在這個庫 13,388 張上的實測。
#
# pHash 只用來粗篩,不做判定(它自己誤判太兇,見檔頭說明)。
PH_HIT = 8          # 只影響「要不要跳過深度搜尋」
PH_MAYBE = 14       # 粗篩的網口

# ORB 判定要「幾何吻合點數」和「吻合點佔配對數的比例」兩個條件一起看。
# 單看任一個都分不開(10 張查詢的實測):
#     點數  真答案最低 44,真誤判最高 60      -> 重疊
#     比例  真答案最低 0.39,真誤判最高 0.45  -> 重疊
# 誤判的特徵是「點數夠但比例低」或「比例夠但點數少」,兩關一起擋才乾淨。
#
# ✓ 的門檻刻意留餘裕:理論上 (40, 0.38) 就能把實測的真假完全分開,
# 但真答案最低 0.39、誤判最高 0.37,差距只有 0.01 —— 10 個樣本上
# 這叫過度擬合。所以 ✓ 拉到 0.45,落在中間的標 ? 讓人自己看一眼。
ORB_HIT_INL = 40
ORB_HIT_RATIO = 0.45
ORB_MIN_INL = 20        # 比這還低的不列出來
ORB_MIN_RATIO = 0.25

# 查詢時把照片轉這些角度各算一次 pHash。pHash 本身不耐旋轉,
# 但算一次只要 0.8 ms,所以乾脆多算幾個角度去補 —— 比直接跑
# 40 秒的 ORB 划算得多。
QUERY_ANGLES = (0, -12, -9, -6, -3, 3, 6, 9, 12, 90, 180, 270)


# ---------------------------------------------------------------- 讀圖

def imread_gray(path):
    """
    讀成灰階。cv2.imread 吃不了中文路徑(Windows 上它用 ANSI 開檔),
    所以一律先用 numpy 讀成位元組再 imdecode。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in EXT_HEIC:
        if not HAS_HEIC:
            return None
        try:
            from PIL import Image
            with Image.open(path) as im:
                return np.array(im.convert("L"))
        except Exception:
            return None
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None


def _shrink(img, side=800):
    """長邊縮到 side。特徵量跟解析度有關,不統一的話比對會失準。"""
    h, w = img.shape[:2]
    s = float(side) / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                      interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- pHash

def too_small(img):
    return (img is None or img.ndim < 2
            or min(img.shape[0], img.shape[1]) < MIN_SIDE)


def phash(img):
    """
    32x32 灰階 -> DCT -> 取左上 8x8 低頻 -> 跟中位數比大小 -> 64 bit。

    丟掉 DC 項([0,0]) 很重要:那一項只代表整體亮度,留著的話
    一張圖調亮之後雜湊就變了,而「調亮還要認得出來」正是我們要的。
    """
    if too_small(img):
        return None
    small = cv2.resize(img, (32, 32), interpolation=cv2.INTER_AREA)
    d = cv2.dct(np.float32(small))[:8, :8].flatten()[1:]
    out = 0
    for b in d > np.median(d):
        out = (out << 1) | int(b)
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


def rotate(img, ang):
    if ang == 0:
        return img
    if ang == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if ang == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if ang == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
    return cv2.warpAffine(img, m, (w, h), borderValue=255)


# ---------------------------------------------------------------- ORB

_orb = None
_bf = None


def _engines():
    global _orb, _bf
    if _orb is None:
        _orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
        _bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    return _orb, _bf


def orb_feats(img):
    """回傳 (keypoint 座標陣列, 描述子)。描述子是 N×32 的 uint8。"""
    if too_small(img):
        return None, None
    orb, _ = _engines()
    try:
        kp, desc = orb.detectAndCompute(_shrink(img), None)
    except cv2.error:
        # 還是有些奇形怪狀的圖會讓金字塔算不下去,跳過比中斷整輪好
        return None, None
    if desc is None or len(kp) < 8:
        return None, None
    pts = np.float32([k.pt for k in kp])
    return pts, desc


def orb_matches(q_desc, desc):
    """
    便宜的第一關:只算描述子配得起來的點數,不做幾何驗證。約 3 ms。

    這個數字當定論用會錯得很慘(實測不同文件誤判 67%),但當篩子
    很好用 —— 它的問題是「不該過的也過」,不是「該過的沒過」。
    實測真正相符的變形圖都拿到 150~276 配對,不同文件平均只有 74,
    所以拿它排序、留前面幾百名,幾乎不會把答案篩掉。
    """
    if q_desc is None or desc is None or len(desc) < 8:
        return None
    _, bf = _engines()
    try:
        return [x for x in bf.match(q_desc, desc) if x.distance < 48]
    except cv2.error:
        return None


def orb_inliers_from(q_pts, pts, m):
    """
    貴的第二關:把配對點丟進 RANSAC 求單應矩陣,算幾何上站得住腳的點數。

    同一張圖的配對點服從同一個幾何變換(旋轉/縮放/透視都算),RANSAC
    會標成 inlier;不同張的配對點是散的,湊不出一致的變換。加上這一關,
    誤判率從 67.05% 降到 0.51%。

    代價很高 —— 實測配對+RANSAC 合計 0.05 秒/張,其中 RANSAC 佔了
    九成以上。所以它只能用在少量候選上,不能全庫掃。
    maxIters 從預設 2000 降到 600:實測足夠穩,又省掉三分之二時間。
    """
    if not m or len(m) < 8:
        return 0
    src = q_pts[[x.queryIdx for x in m]].reshape(-1, 1, 2)
    dst = pts[[x.trainIdx for x in m]].reshape(-1, 1, 2)
    try:
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0, maxIters=600)
    except cv2.error:
        return 0
    return 0 if mask is None else int(mask.sum())


def orb_inliers(q_pts, q_desc, pts, desc):
    """兩關一起跑。候選不多的時候用這個就好。"""
    m = orb_matches(q_desc, desc)
    return orb_inliers_from(q_pts, pts, m) if m else 0


# ---------------------------------------------------------------- 資料庫

def feat_db_path(data_dir):
    """
    特徵另存一個檔,不塞進 index.db。

    兩個理由:index.db 要被 pack 帶到另一台(已經 224 MB),再加 144 MB
    的特徵太肥;而且特徵隨時可以重建,不像 OCR 文字跑一次要八小時。
    """
    return os.path.join(data_dir, "imgfeat.db")


def feat_open(data_dir):
    p = feat_db_path(data_dir)
    os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS imgs(
            path   TEXT PRIMARY KEY,
            mtime  REAL,
            size   INTEGER,
            w      INTEGER,
            h      INTEGER,
            phash  INTEGER,
            pts    BLOB,      -- float32 N×2  keypoint 座標
            desc   BLOB,      -- uint8   N×32  ORB 描述子
            nfeat  INTEGER
        );
        CREATE INDEX IF NOT EXISTS imgs_ph ON imgs(phash);
    """)
    return con


def _pack(pts, desc):
    if pts is None or desc is None:
        return None, None, 0
    return pts.astype(np.float32).tobytes(), desc.astype(np.uint8).tobytes(), len(desc)


def _unpack(pts_b, desc_b, n):
    if not pts_b or not desc_b or not n:
        return None, None
    pts = np.frombuffer(pts_b, dtype=np.float32).reshape(n, 2)
    desc = np.frombuffer(desc_b, dtype=np.uint8).reshape(n, 32)
    return pts, desc


# ---------------------------------------------------------------- 建立特徵

def scan_images(roots):
    """走訪 roots 底下所有圖片。沿用 findpile 的跳過規則。"""
    import findpile
    out = []
    want = EXT_IMG | EXT_HEIC
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn
                     if d not in findpile.SKIP_DIRS
                     and not d.startswith("$")
                     and not d.startswith(".")
                     and not d.lower().endswith(findpile.SKIP_SUFFIX)]
            for f in fn:
                if f.startswith("._"):            # macOS 的 resource fork
                    continue
                if os.path.splitext(f)[1].lower() in want:
                    out.append(os.path.join(dp, f))
    return out


def build(roots, data_dir, redo=False, limit=None, progress=None):
    """
    建立/更新圖片特徵。可重複執行,只處理新的或改過的。

    回傳統計 dict。
    """
    if cv2 is None:
        raise RuntimeError("需要 opencv-python。請執行:pip install opencv-python")

    con = feat_open(data_dir)
    known = {p: (m, s) for p, m, s in
             con.execute("SELECT path,mtime,size FROM imgs")}

    files = scan_images(roots)
    todo = []
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        old = known.get(p)
        if redo or old is None or abs(old[0] - st.st_mtime) > 1 or old[1] != st.st_size:
            todo.append((p, st.st_mtime, st.st_size))
    if limit:
        todo = todo[:limit]

    stat = {"found": len(files), "todo": len(todo), "done": 0,
            "failed": 0, "heic_skipped": 0, "too_small": 0}
    if not todo:
        # 清掉已經不存在的
        gone = [p for p in known if not os.path.exists(p)]
        if gone:
            con.executemany("DELETE FROM imgs WHERE path=?", [(p,) for p in gone])
            con.commit()
        stat["removed"] = len(gone)
        con.close()
        return stat

    t0 = time.time()
    buf = []
    for i, (p, mt, sz) in enumerate(todo, 1):
        ext = os.path.splitext(p)[1].lower()
        img = imread_gray(p)
        if img is None:
            stat["failed"] += 1
            if ext in EXT_HEIC and not HAS_HEIC:
                stat["heic_skipped"] += 1
            # 記一筆空的,免得每次都重試讀不了的檔
            buf.append((p, mt, sz, 0, 0, None, None, None, 0))
        elif too_small(img):
            # 圖示、佔位檔之類的小圖。照樣記一筆(免得每次重試),
            # 但 phash 留 NULL、nfeat=0,搜尋時這兩個條件都會把它濾掉。
            h, w = img.shape[:2]
            buf.append((p, mt, sz, w, h, None, None, None, 0))
            stat["too_small"] += 1
        else:
            h, w = img.shape[:2]
            ph = phash(img)
            pts, desc = orb_feats(img)
            pb, db_, n = _pack(pts, desc)
            buf.append((p, mt, sz, w, h, ph, pb, db_, n))
            stat["done"] += 1

        if len(buf) >= 200:
            con.executemany("INSERT OR REPLACE INTO imgs"
                            "(path,mtime,size,w,h,phash,pts,desc,nfeat)"
                            " VALUES (?,?,?,?,?,?,?,?,?)", buf)
            con.commit()
            buf = []
        if progress and (i % 100 == 0 or i == len(todo)):
            el = time.time() - t0
            progress(i, len(todo), el, el / i * (len(todo) - i))

    if buf:
        con.executemany("INSERT OR REPLACE INTO imgs"
                        "(path,mtime,size,w,h,phash,pts,desc,nfeat)"
                        " VALUES (?,?,?,?,?,?,?,?,?)", buf)
        con.commit()

    gone = [p for p in known if not os.path.exists(p)]
    if gone:
        con.executemany("DELETE FROM imgs WHERE path=?", [(p,) for p in gone])
        con.commit()
    stat["removed"] = len(gone)
    stat["elapsed"] = time.time() - t0
    con.close()
    return stat


# ---------------------------------------------------------------- 搜尋

def judge(inl, nmatch):
    """
    (要不要列出, 是不是 ✓, 比例)。門檻判斷只寫在這裡一處。
    """
    ratio = (inl / float(nmatch)) if nmatch else 0.0
    keep = inl >= ORB_MIN_INL and ratio >= ORB_MIN_RATIO
    sure = inl >= ORB_HIT_INL and ratio >= ORB_HIT_RATIO
    return keep, sure, ratio


def _mk(path, w, h, dist, inliers, sure, how, ratio=None):
    """
    統一的結果格式。detail 直接給好,免得 CLI 和 GUI 各寫一套門檻判斷
    —— 門檻改了只改這裡一處。
    """
    if inliers is not None:
        # 分數同時反映點數和比例:點數再多,比例低就不該排前面
        score = min(100.0, 100.0 * inliers / 100.0) * (ratio if ratio else 1.0)
        detail = ("%d 點 %.0f%%" % (inliers, ratio * 100)) if ratio is not None \
            else "%d 點" % inliers
    else:
        score = 100.0 * (64 - dist) / 64.0
        detail = "差異 %d" % dist
    return {"path": path, "w": w, "h": h, "dist": dist, "inliers": inliers,
            "ratio": ratio, "sure": sure, "how": how,
            "detail": detail, "score": score}


def phash_ranked(query_img, data_dir):
    """
    照 pHash 距離把整個索引排序。13,570 張只要千分之幾秒。

    把照片轉 12 個角度各算一次雜湊取最小值 —— pHash 不耐旋轉,
    多算幾次來補,比直接跑 ORB 便宜太多。

    回傳 [(path, dist, w, h), ...],由近到遠。
    """
    con = feat_open(data_dir)
    qhs = []
    for a in QUERY_ANGLES:
        h = phash(rotate(query_img, a))
        if h is not None:
            qhs.append(h)
    out = []
    for path, ph, w, h in con.execute(
            "SELECT path,phash,w,h FROM imgs WHERE phash IS NOT NULL"):
        out.append((path, min(hamming(qh, ph) for qh in qhs), w, h))
    con.close()
    out.sort(key=lambda r: r[1])
    return out


def phash_candidates(query_img, data_dir, limit=150):
    """
    粗篩:只留距離在 PH_MAYBE 以內的。

    網刻意撒寬,因為結果一定會再過 ORB 驗證 —— 寧可多撈幾張讓驗證
    去刷掉,也不要在這一關就把答案篩掉。
    """
    ranked = phash_ranked(query_img, data_dir)
    return [r for r in ranked if r[1] <= PH_MAYBE][:limit]


def verify(query_img, cands, data_dir):
    """
    對 pHash 撈出來的候選跑 ORB + 幾何驗證。

    為什麼非驗不可:實測 40 張查詢對全庫 13,388 張,就算 pHash 門檻
    收到 2,平均每次還是誤判 0.4 張 —— 因為文件掃描檔在 8×8 DCT
    的解析度下本來就幾乎一樣(大片白、同樣信箋頭、同樣表格)。
    pHash 只配當粗篩,沒有資格說「確定相符」。

    候選只有幾十張,所以這一關很便宜(每張約 5 ms)。
    """
    if not cands:
        return []
    q_pts, q_desc = orb_feats(query_img)
    con = feat_open(data_dir)
    feats = {}
    # SQLite 的參數上限是 999,分批查
    paths = [c[0] for c in cands]
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for p, pb, db_, n in con.execute(
                "SELECT path,pts,desc,nfeat FROM imgs WHERE path IN (%s)" % ph, chunk):
            feats[p] = _unpack(pb, db_, n)
    con.close()

    out = []
    for path, dist, w, h in cands:
        pts, desc = feats.get(path, (None, None))
        if q_desc is None:
            continue
        m = orb_matches(q_desc, desc)
        if not m:
            continue
        inl = orb_inliers_from(q_pts, pts, m)
        _keep, sure, ratio = judge(inl, len(m))
        # 第一關只收 ✓ —— 沒把握的留給深度搜尋去全庫找,
        # 不要拿「可能相關」就把流程停掉
        if sure:
            out.append(_mk(path, w, h, dist, inl, True, "快篩+驗證", ratio))
    out.sort(key=lambda r: -r["inliers"])
    return out


# 深度搜尋預設只掃 pHash 排序的前這麼多張。
#
# 為什麼可以這樣省:實測同一張圖做各種變形,看正確答案在 pHash 距離
# 排序裡排第幾(全庫 13,570 張)——
#     原圖 / 旋轉11度 / 旋轉11度+裁8%+透視   -> 第 1 名
#     旋轉30度 + 裁20%                      -> 第 493 名
#     旋轉45度 + 裁25% + 透視               -> 第 1,909 名
# 就算歪成那樣,答案都還在前 2,000 名裡。所以掃前 3,000 張已經很寬裕,
# 而成本只有全掃的五分之一(130 秒 -> 約 30 秒)。
# 真的要滴水不漏,findimg 加 --full。
# 深度搜尋第一關用幾個查詢描述子。BFMatcher 的成本跟兩邊描述子數量
# 相乘,所以砍查詢端最有效。實測全庫 13,388 張:
#     600 個 -> 40~44 秒     300 個 -> 22 秒     200 個 -> 15~20 秒
# 而且三種設定下,正確答案都穩定排在第 1~2 名 —— 減描述子不傷排名品質。
# 取 300 是留一點餘裕(樣本只有 5 張,不想壓到極限)。
ROUGH_DESC = 300

# 深度搜尋第二關(RANSAC 幾何驗證)只跑配對數最高的這麼多張。
# RANSAC 是 0.05 秒/張,不可能全庫跑。
# 既然實測正確答案在配對數排名裡都落在第 1~2 名,120 張已經是
# 六十倍的餘裕,還能順便多報一些「可能相關」給使用者看。
RANSAC_N = 120


def search_deep(query_img, data_dir, top=60, progress=None, rough_desc=ROUGH_DESC):
    """
    ORB + RANSAC 掃全庫。pHash 判不出來的就靠這個:旋轉、裁切、
    手持拍攝的歪斜它都認得。

    ⚠️ 這裡刻意**不用 pHash 預篩**。原本為了省時間只掃 pHash 排序的
    前 3,000 名,實測 10 張模擬拍照的查詢,召回率只有 7/10 ——
    三張失敗的全是被預篩擋在門外(pHash 排第 4,960 / 6,602 / 3,835 名),
    可是它們的 RANSAC inlier 分別是 117 / 241 / 44,遠超 ✓ 的門檻 25。
    不是演算法認不出來,是根本沒被放進來比。

    原因:pHash 距離對重度變形的圖沒有鑑別力(那三張距離都 24~26,
    跟隨機圖差不多)。拿它決定掃誰,等於隨機丟掉一半的庫。

    rough_desc=None 表示第一關用全部描述子(更慢,幾乎不會更準)。
    """
    q_pts, q_desc = orb_feats(query_img)
    if q_desc is None:
        return []
    q_rough = q_desc[:rough_desc] if rough_desc else q_desc

    con = feat_open(data_dir)
    rows = con.execute("SELECT path,pts,desc,nfeat,w,h FROM imgs"
                       " WHERE nfeat > 0").fetchall()
    con.close()

    # ---- 第一關:全庫都算配對數(便宜,查詢端只用 300 個描述子)
    #      只留下排名用的數字和原始特徵,RANSAC 留給第二關。
    t0 = time.time()
    rough = []
    for i, (path, pb, db_, n, w, h) in enumerate(rows, 1):
        pts, desc = _unpack(pb, db_, n)
        m = orb_matches(q_rough, desc)
        if m and len(m) >= 8:
            rough.append((len(m), path, pts, desc, w, h))
        if progress and (i % 200 == 0 or i == len(rows)):
            el = time.time() - t0
            # 第二關的成本也算進總量,不然進度條會顯示「快好了」然後卡住
            total = len(rows) + RANSAC_N
            progress(i, total, el, el / i * (total - i))

    # ---- 第二關:只對配對數最高的那些跑 RANSAC(貴,約 50 ms/張)
    #
    # 這裡用**完整的** 600 個查詢描述子重新配對,不沿用第一關的結果。
    # 第一關只用 300 個,配出來的點數少一半,inlier 也跟著少一半 ——
    # 實測有一張真正相符的圖 inlier 44,減半後約 22,會低於 ✓ 的門檻 25
    # 而被降級成「可能相關」。重配只有 120 張,多花不到半秒,很值得。
    rough.sort(key=lambda r: -r[0])
    rough = rough[:RANSAC_N]
    out = []
    for j, (_nm, path, pts, desc, w, h) in enumerate(rough, 1):
        m = orb_matches(q_desc, desc)
        inl = orb_inliers_from(q_pts, pts, m) if m else 0
        keep, sure, ratio = judge(inl, len(m) if m else 0)
        if keep:
            out.append(_mk(path, w, h, None, inl, sure, "深度", ratio))
        if progress and (j % 25 == 0 or j == len(rough)):
            el = time.time() - t0
            done = len(rows) + j
            total = len(rows) + len(rough)
            progress(done, total, el, el / done * (total - done))

    # 用 score 排(點數 × 比例),不要只看點數 —— 實測有誤判拿到
    # 172 點但比例只有 0.37,只看點數會讓它壓在真答案前面
    out.sort(key=lambda r: -r["score"])
    return out[:top]


def search(query_path, data_dir, deep=False, top=60, progress=None,
           rough_desc=ROUGH_DESC):
    """
    主入口。先 pHash;沒有足夠好的結果才自動補上 ORB 深度搜尋。

    回傳 (結果清單, 說明文字)。
    """
    img = imread_gray(query_path)
    if img is None:
        ext = os.path.splitext(query_path)[1].lower()
        if ext in EXT_HEIC and not HAS_HEIC:
            raise RuntimeError(
                "這是 HEIC(iPhone 照片),需要外掛才讀得到。\n"
                "請執行:pip install pillow-heif")
        raise RuntimeError("讀不到這張圖:%s" % query_path)

    # ---- 第一關:pHash 粗篩 + ORB 驗證。常見情形在這裡就結束,不到一秒。
    cands = []
    if not deep:
        cands = phash_candidates(img, data_dir)
        hit = verify(img, cands, data_dir)
        if hit:
            return hit, "秒出:%d 張確定相符(快篩後經幾何驗證)" % len(hit)

    # ---- 第二關:ORB 全量掃。查詢圖轉太多角度或只拍到一部分時才需要,
    #      因為那種情況 pHash 粗篩根本撈不到正確答案。
    deep_res = search_deep(img, data_dir, top, progress, rough_desc)
    if deep_res:
        n_sure = sum(1 for r in deep_res if r["sure"])
        return deep_res, ("深度搜尋:%d 張確定相符、%d 張可能相關"
                          % (n_sure, len(deep_res) - n_sure))

    # ---- 都空手:把 pHash 的候選當「長得像」還回去,但明講沒通過驗證。
    #      不還的話這批結果就白算了,使用者看到「找不到」卻不知道其實
    #      有幾張很接近 —— 寧可給他看,讓他自己用眼睛判斷。
    if cands:
        out = [_mk(p, w, h, d, None, False, "僅快篩")
               for p, d, w, h in cands[:top]]
        return out, ("沒有確定相符的。以下 %d 張只是「版面接近」,"
                     "沒通過幾何驗證,請自己看一眼" % len(out))
    return [], "找不到相似的圖片"


def stats(data_dir):
    p = feat_db_path(data_dir)
    if not os.path.exists(p):
        return None
    con = feat_open(data_dir)
    n, ok, bad = con.execute(
        "SELECT COUNT(*), SUM(nfeat>0), SUM(nfeat=0) FROM imgs").fetchone()
    con.close()
    return {"total": n or 0, "ok": ok or 0, "failed": bad or 0,
            "db_mb": os.path.getsize(p) / 1e6}
