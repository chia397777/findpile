#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
app.py —— findpile 的新介面(PySide6 / Qt6)

為什麼不直接改 gui.py:
  Tkinter 的外觀受制於各平台原生主題,圓角、hover、焦點框、字距
  都調不動;而且沒有拖放、沒有縮圖牆、剪貼簿只能靠 PIL。
  這些都不是「調參數」能解決的,所以另開一支用 Qt 寫。

  gui.py 一行沒動,留著當退路 —— 這台機器的 Qt 若壞了
  (顯卡驅動、OpenGL、DLL 缺件都可能),舊介面照樣能搜。

開啟方式:
  Windows   搜尋視窗_新.bat
  其他       python app.py

比舊介面多的東西:
  ·  結果列改成兩行:檔名一行、資料夾一行,不必再左右拉
  ·  照片模式的縮圖牆,縮圖在背景執行緒讀,不卡畫面
  ·  以圖搜圖可以直接把圖拖進視窗,或按 Ctrl+V 貼上
  ·  查詢圖和找到的圖並排比對(舊介面是上下排,要捲動)
  ·  深度搜尋有進度條
  ·  關鍵字搜到的圖片檔也能切成縮圖看
"""

import html
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    # 桌面捷徑直接指向這支程式,而且是用 pythonw 開的 —— pythonw 沒有
    # stderr,在這裡印訊息等於沒印,使用者只會看到「雙擊沒反應」。
    # 所以缺 PySide6 時不是結束,是把舊視窗開起來:至少還能搜尋。
    # 兩個視窗長得完全不一樣,使用者一看就知道走了退路。
    sys.stderr.write(
        "找不到 PySide6,改開舊介面(gui.py)。\n"
        "要用新介面請執行:python -m pip install --user PySide6\n")
    import runpy
    runpy.run_path(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui.py"),
        run_name="__main__")
    raise SystemExit(0)

import findpile                                      # noqa: E402

# 以圖搜圖是附加功能。缺 opencv 不該讓整個視窗開不起來,
# 按鈕按下去才告訴使用者缺什麼。
try:
    import imgfind
    IMG_ERR = None
except Exception as _e:                             # noqa: BLE001
    imgfind = None
    IMG_ERR = str(_e)

# imgfind 掛掉時仍要判斷哪些結果是圖片,所以這份清單不能依賴它
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
           ".heic", ".heif"}

# ---------------------------------------------------------------- 外觀

BG = "#f6f6f4"        # 視窗底色
CARD = "#ffffff"
LINE = "#e3e1db"      # 分隔線
TEXT = "#26251f"
MUTED = "#8a877d"
STALE = "#b4732a"          # 索引過期時「更新索引」按鈕的顏色

# 表格欄位。檔案欄永遠是最後一欄(兩行繪製的 delegate 掛在上面),
# 日期欄在它前面。用常數而不是散落的數字 —— 插一個欄位就要全檔找
# 「哪個 4 是欄位、哪個 4 是別的東西」,那種錯只會在執行時才炸。
COLS = ("#", "分數", "命中", "字數", "日期", "檔案")
DATECOL = len(COLS) - 2
NAMECOL = len(COLS) - 1

# 時間篩選的選項。值是天數,None = 不限。
SPANS = [("不限", None), ("最近 7 天", 7), ("最近 30 天", 30),
         ("最近一年", 365), ("最近三年", 365 * 3)]
SORTS = ["相關度", "最新在前", "最舊在前"]
ACCENT = "#1a5490"
ACCENT_D = "#143f69"
SEL = "#e4eef8"       # 選取列
HILITE = "#fff2bf"    # 關鍵字反黃
OCRCLR = "#8a5a00"
OK = "#1d7a4c"

# 順序有意義:繁體字面的排前面。簡體字面的黑體(YaHei)排最後,
# 部分字的字形會變成大陸寫法。
FONT_PREFS = (
    "Microsoft JhengHei UI", "Microsoft JhengHei", "微軟正黑體",
    "PingFang TC", "Heiti TC", "Apple LiGothic",
    "Noto Sans CJK TC", "Microsoft YaHei",
)

QSS = """
QWidget          { color: %(TEXT)s; font-size: 11pt; }
QMainWindow,
#page            { background: %(BG)s; }

/* ---- 上面的搜尋卡片 ---- */
#bar {
    background: %(CARD)s;
    border: 1px solid %(LINE)s;
    border-radius: 10px;
}
#kw {
    border: 1px solid %(LINE)s;
    border-radius: 7px;
    padding: 7px 10px;
    font-size: 14pt;
    background: %(CARD)s;
    selection-background-color: %(ACCENT)s;
    selection-color: white;
}
#kw:focus         { border: 2px solid %(ACCENT)s; padding: 6px 9px; }
#hint             { color: %(MUTED)s; font-size: 9pt; }

QPushButton {
    background: #eceae4;
    border: 1px solid %(LINE)s;
    border-radius: 7px;
    padding: 7px 13px;
    font-size: 10pt;
}
QPushButton:hover   { background: #e2dfd7; }
QPushButton:pressed { background: #d6d3ca; }
/* 索引過期時的「更新索引」。用 property 切換,不是換物件 ——
   換 styleSheet 字串會讓整個樣式表重算,整個視窗閃一下 */
QPushButton[stale="true"] {
    background: #fff2bf;
    border-color: %(STALE)s;
    color: %(STALE)s;
    font-weight: bold;
}
QPushButton[stale="true"]:hover { background: #ffe9a8; }
QPushButton:disabled{ color: #b5b2a9; background: #f2f1ec; }

#go {
    background: %(ACCENT)s; color: white; border: none;
    border-radius: 7px; padding: 9px 22px;
    font-size: 11pt; font-weight: bold;
}
#go:hover           { background: %(ACCENT_D)s; }
#go:disabled        { background: #9bb2c6; }

/* 分段切換(任一/全部、列表/縮圖):靠在一起、選到的反白 */
#seg {
    background: #eceae4; border: 1px solid %(LINE)s;
    border-radius: 7px; padding: 6px 14px; font-size: 10pt;
}
#seg:checked   { background: %(ACCENT)s; color: white; border-color: %(ACCENT)s; }
#seg:hover:!checked { background: #e2dfd7; }

QCheckBox        { font-size: 10pt; spacing: 6px; }

/* ---- 結果表 ---- */
QTableView, QListView, QTextBrowser {
    background: %(CARD)s;
    border: 1px solid %(LINE)s;
    border-radius: 8px;
    selection-background-color: %(SEL)s;
    selection-color: %(TEXT)s;
    outline: none;
}
QTableView        { alternate-background-color: #fbfaf7; gridline-color: transparent; }
QTableView::item  { padding: 0px; border: none; }
QHeaderView::section {
    background: #f0eee8; color: %(MUTED)s;
    border: none; border-bottom: 1px solid %(LINE)s;
    padding: 7px 8px; font-size: 9pt; font-weight: bold;
}
QListView::item:selected { background: %(SEL)s; border-radius: 6px; }

QScrollBar:vertical   { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #cfccc3; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b6b3a9; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: #cfccc3; border-radius: 5px; min-width: 30px;
}

#crumb            { color: %(ACCENT)s; font-size: 10pt; font-weight: bold; }
#status           { color: #4a483f; font-size: 9pt; }
QSplitter::handle { background: transparent; height: 10px; }
QProgressBar {
    border: 1px solid %(LINE)s; border-radius: 5px; height: 10px;
    background: %(CARD)s; text-align: center; font-size: 8pt;
}
QProgressBar::chunk { background: %(ACCENT)s; border-radius: 4px; }
#pane             { background: %(CARD)s; border: 1px solid %(LINE)s;
                    border-radius: 8px; }
""" % globals()


def pick_font():
    """挑一個這台機器真的有、而且有繁中字的黑體。"""
    have = set(QtGui.QFontDatabase.families())
    for f in FONT_PREFS:
        if f in have:
            return f
    return QtWidgets.QApplication.font().family()


def is_img(path):
    return os.path.splitext(path)[1].lower() in IMG_EXT


# ---------------------------------------------------------------- 執行緒

class Task(QtCore.QThread):
    """
    把一個函式丟到背景跑,結果用 signal 送回主執行緒。

    Qt 的規矩和 Tkinter 一樣:只有主執行緒能碰介面。
    signal/slot 跨執行緒時 Qt 會自動排進主執行緒的事件迴圈,
    所以這裡不必像 gui.py 那樣到處寫 root.after(0, ...)。
    """
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    tick = QtCore.Signal(int, int, float, float)
    line = QtCore.Signal(str)        # 背景工作印出來的一行進度

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn(self))
        except Exception as e:                      # noqa: BLE001
            self.failed.emit(str(e))


class _ThumbSig(QtCore.QObject):
    ready = QtCore.Signal(int, int, object)         # 第幾列, 世代, QImage


class ThumbJob(QtCore.QRunnable):
    """讀一張縮圖。縮圖牆有幾百張,一張一張同步讀會卡住畫面好幾秒。"""

    def __init__(self, row, gen, path, box, sig):
        super().__init__()
        self.row, self.gen, self.path, self.box, self.sig = \
            row, gen, path, box, sig

    def run(self):
        # 這裡是工作執行緒,只能產 QImage。QPixmap 是 Qt 明文規定
        # 只能在主執行緒碰的東西(它背後接的是繪圖裝置),在這裡建
        # 在 Windows 上目前不會當掉,但那是未定義行為。
        self.sig.ready.emit(self.row, self.gen, read_image(self.path, self.box))


def read_image(path, box):
    """
    讀成不超過 box 的縮圖,回傳 QImage(失敗回 None)。執行緒安全。

    用 QImageReader 的 setScaledSize:解碼時就縮,不必把 4000x3000
    的整張 JPEG 讀進記憶體再縮 —— 幾百張的差別很有感。
    setAutoTransform 是給手機照片用的,EXIF 裡的旋轉要照著轉,
    不然橫拍的照片在縮圖牆裡全部躺著。
    """
    try:
        rd = QtGui.QImageReader(path)
        rd.setAutoTransform(True)
        sz = rd.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            s = min(box / float(sz.width()), box / float(sz.height()), 1.0)
            rd.setScaledSize(QtCore.QSize(max(1, int(sz.width() * s)),
                                          max(1, int(sz.height() * s))))
        img = rd.read()
        if not img.isNull():
            return img
    except Exception:                               # noqa: BLE001
        pass
    return _pil_thumb(path, box)


def read_thumb(path, box):
    """read_image 的 QPixmap 版。只能在主執行緒呼叫。"""
    img = read_image(path, box)
    return None if img is None else QtGui.QPixmap.fromImage(img)


def square(pm, box):
    """
    把縮圖墊進一個 box×box 的方框裡(置中,不變形)。

    不墊的話,橫幅的圖只有 150x100、直幅的 100x150,Qt 會緊接著圖
    下緣畫說明文字 —— 整面縮圖牆的文字就高高低低參差不齊。
    墊成一樣高之後,每一格的文字都對齊同一條基線。
    """
    if pm is None or pm.isNull():
        return None
    out = QtGui.QPixmap(box, box)
    out.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(out)
    p.drawPixmap((box - pm.width()) // 2, (box - pm.height()) // 2, pm)
    p.end()
    return out


_HEIF_TRIED = False


def _heif():
    """
    讓 PIL 認得 HEIC。只做一次。

    imgfind 匯入時也會做同一件事,但那是 import 的副作用 —— imgfind
    匯入失敗(上面會把它設成 None)時就沒人註冊了,關鍵字結果裡的
    .heic 縮圖會整片空白。所以這裡自己再保一道,不靠別人。
    """
    global _HEIF_TRIED
    if _HEIF_TRIED:
        return
    _HEIF_TRIED = True
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                               # noqa: BLE001
        pass                                        # 沒裝就算了,下面會回 None


def _pil_thumb(path, box):
    """Qt 認不出來的格式(主要是 iPhone 的 HEIC)交給 PIL。"""
    try:
        _heif()
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((box, box), Image.LANCZOS)
            data = im.tobytes("raw", "RGB")
            qi = QtGui.QImage(data, im.width, im.height,
                              im.width * 3, QtGui.QImage.Format_RGB888)
            # 一定要 copy():QImage 這個建構式不會接管 data 的所有權,
            # data 被回收後原本的 QImage 就指向一塊死掉的記憶體
            return qi.copy()
    except Exception:                               # noqa: BLE001
        return None


# ---------------------------------------------------------------- 繪製

FOLDER = QtCore.Qt.UserRole + 1
BADGE = QtCore.Qt.UserRole + 2
ROWIDX = QtCore.Qt.UserRole + 3


class TwoLine(QtWidgets.QStyledItemDelegate):
    """
    檔案欄畫成兩行:上面檔名(粗體)、下面資料夾(灰色小字)。

    舊介面把檔名和資料夾分成兩欄,路徑一長就得左右拉。
    改成兩行之後整列都看得完,而且右邊還放得下 OCR / ✓ 的標籤。
    """

    def paint(self, p, opt, idx):
        self.initStyleOption(opt, idx)
        name = idx.data(QtCore.Qt.DisplayRole) or ""
        folder = idx.data(FOLDER) or ""
        badge = idx.data(BADGE) or ""

        # 背景(選取色、交替色)交給原本的樣式畫,自己只畫文字
        o = QtWidgets.QStyleOptionViewItem(opt)
        o.text = ""
        w = opt.widget
        style = w.style() if w else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, o, p, w)

        r = opt.rect.adjusted(9, 5, -9, -5)
        p.save()
        fb = QtGui.QFont(opt.font)
        fb.setBold(True)
        fmb = QtGui.QFontMetrics(fb)
        fs = QtGui.QFont(opt.font)
        fs.setPointSizeF(max(7.5, opt.font.pointSizeF() - 1.5))
        fms = QtGui.QFontMetrics(fs)

        avail = r.width()
        if badge:
            txt, bg, fg = badge
            bw = fms.horizontalAdvance(txt) + 14
            pill = QtCore.QRect(r.right() - bw, r.top() + 1, bw,
                                fmb.height() - 2)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(bg))
            p.drawRoundedRect(pill, 7, 7)
            p.setFont(fs)
            p.setPen(QtGui.QColor(fg))
            p.drawText(pill, QtCore.Qt.AlignCenter, txt)
            avail -= bw + 8

        p.setFont(fb)
        p.setPen(QtGui.QColor(TEXT))
        p.drawText(QtCore.QRect(r.left(), r.top(), avail, fmb.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fmb.elidedText(name, QtCore.Qt.ElideMiddle, avail))

        p.setFont(fs)
        p.setPen(QtGui.QColor(MUTED))
        p.drawText(QtCore.QRect(r.left(), r.top() + fmb.height() + 1,
                                r.width(), fms.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fms.elidedText(folder, QtCore.Qt.ElideLeft, r.width()))
        p.restore()

    def sizeHint(self, opt, idx):
        return QtCore.QSize(200, 46)


class PhotoView(QtWidgets.QLabel):
    """
    會跟著面板大小縮放的圖片框。

    QLabel 配 setScaledContents 會把圖片拉歪,所以自己留著原圖,
    每次 resize 重算一次等比例縮放。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumSize(80, 80)
        self.setStyleSheet("background:#f0eee8; border-radius:6px;")

    def set_path(self, path):
        self._src = read_thumb(path, 1400) if path else None
        if self._src is None:
            self.setText("(這張圖顯示不出來)")
            self.setStyleSheet("background:#f0eee8; border-radius:6px;"
                               "color:%s;" % MUTED)
        self._fit()

    def _fit(self):
        if self._src is None:
            return
        self.setPixmap(self._src.scaled(
            self.size(), QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


# ---------------------------------------------------------------- 主視窗

class Win(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.rows = []
        self.docrows = []        # 有內文的結果
        self.namerows = []       # 只靠檔名/資料夾名稱命中的結果
        self.scope = 0
        self._last = ("any", False, 0.0)
        self._stale_at = 0.0     # 上次檢查索引是否過期的時間
        self._stale_t = None     # 檢查用的背景執行緒(和搜尋分開放)
        self._nstale = 0
        self.kws = []
        self.droots = [findpile.DEFAULT_ROOT]
        self.img_mode = False
        self.query_path = None
        self._task = None
        self._gen = 0            # 搜尋世代:舊搜尋晚一步回來不該蓋掉新結果
        self._thumb_gen = 0
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._tsig = _ThumbSig()
        self._tsig.ready.connect(self._thumb_ready)

        self.setWindowTitle("檔案堆搜尋 — findpile")
        self.resize(1240, 800)
        self.setMinimumSize(940, 580)
        self.setAcceptDrops(True)

        page = QtWidgets.QWidget(objectName="page")
        self.setCentralWidget(page)
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.setSpacing(10)
        lay.addWidget(self._bar())
        lay.addWidget(self._split(), 1)
        self._statusbar()
        self._shortcuts()

        QtCore.QTimer.singleShot(60, self.load_db)

    # ------------------------------------------------------------ 介面

    def _bar(self):
        box = QtWidgets.QFrame(objectName="bar")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(14, 12, 14, 11)
        v.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)
        self.kw = QtWidgets.QLineEdit(objectName="kw")
        self.kw.setPlaceholderText("輸入關鍵字,用空白分開…")
        self.kw.setClearButtonEnabled(True)
        self.kw.returnPressed.connect(self.go)
        top.addWidget(self.kw, 1)

        self.btn_go = QtWidgets.QPushButton("搜尋", objectName="go")
        self.btn_go.clicked.connect(self.go)
        top.addWidget(self.btn_go)

        self.btn_idx = QtWidgets.QPushButton("⟳ 更新索引")
        self.btn_idx.setToolTip(
            "重新掃描索引收錄過的資料夾,把新增和修改過的檔案收進來。\n"
            "索引是一份快照,不會自己更新 —— 剛存的檔案要跑過這個才搜得到。")
        self.btn_idx.clicked.connect(self.reindex)

        self.btn_img = QtWidgets.QPushButton("📷 以圖搜圖")
        self.btn_img.setToolTip("選一張照片,或直接把圖拖進視窗 / 按 Ctrl+V 貼上")
        self.btn_img.clicked.connect(self.go_image)
        top.addWidget(self.btn_img)
        v.addLayout(top)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        self.m_any = QtWidgets.QPushButton("任一出現", objectName="seg",
                                           checkable=True, checked=True)
        self.m_all = QtWidgets.QPushButton("全部出現", objectName="seg",
                                           checkable=True)
        grp = QtWidgets.QButtonGroup(self)
        grp.setExclusive(True)
        for b in (self.m_any, self.m_all):
            grp.addButton(b)
            row.addWidget(b)
        row.addSpacing(14)

        self.loose = QtWidgets.QCheckBox("模糊比對")
        self.loose.setToolTip("OCR 掃描檔常有錯字,長關鍵字會整個比對不到。\n"
                              "打開後改用二字組比對,一兩個錯字還是找得到。")
        row.addWidget(self.loose)
        row.addSpacing(18)

        # 改條件後重搜一次。舊介面要自己再按一下 Enter,
        # 很容易以為「改了沒反應」。
        for b in (self.m_any, self.m_all, self.loose):
            b.clicked.connect(self._redo)

        self.v_list = QtWidgets.QPushButton("📋 列表", objectName="seg",
                                           checkable=True, checked=True)
        self.v_grid = QtWidgets.QPushButton("▦ 縮圖", objectName="seg",
                                            checkable=True)
        vg = QtWidgets.QButtonGroup(self)
        vg.setExclusive(True)
        for b in (self.v_list, self.v_grid):
            vg.addButton(b)
            row.addWidget(b)
        self.v_list.clicked.connect(lambda: self.set_view(0))
        self.v_grid.clicked.connect(lambda: self.set_view(1))
        self.v_grid.setEnabled(False)
        row.addSpacing(18)

        # 「有內文」和「只有名稱」分兩區,不混排。影片、音檔、還沒 OCR 的
        # 圖片抽不出內文,分數只能靠檔名湊,跟文件擺在一起排名會互相干擾
        # —— 查一個相簿資料夾名會一次噴出幾千張照片,把文件壓到看不見。
        self.sc_doc = QtWidgets.QPushButton("📄 有內文", objectName="seg",
                                            checkable=True, checked=True)
        self.sc_name = QtWidgets.QPushButton("🏷 只有名稱", objectName="seg",
                                             checkable=True)
        sg = QtWidgets.QButtonGroup(self)
        sg.setExclusive(True)
        for b in (self.sc_doc, self.sc_name):
            sg.addButton(b)
            row.addWidget(b)
        self.sc_doc.clicked.connect(lambda: self.set_scope(0))
        self.sc_name.clicked.connect(lambda: self.set_scope(1))
        self.sc_name.setEnabled(False)

        row.addStretch(1)
        self.hint = QtWidgets.QLabel("掃描檔用短關鍵字(2 字)比較找得到",
                                     objectName="hint")
        row.addWidget(self.hint)
        v.addLayout(row)

        # ---- 第二列:類型 / 時間 / 排序
        # 一次查出幾千筆的時候,真正缺的不是更準的排名,是「只看 PDF」
        # 或「只看今年」。這三個都在記憶體裡篩,不重查資料庫,所以是即時的。
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)

        def lab(t):
            w = QtWidgets.QLabel(t, objectName="hint")
            row2.addWidget(w)

        lab("類型")
        self.f_kind = QtWidgets.QComboBox()
        self.f_kind.setMinimumWidth(132)
        self.f_kind.addItem("全部", None)
        row2.addWidget(self.f_kind)
        row2.addSpacing(10)

        lab("時間")
        self.f_time = QtWidgets.QComboBox()
        self.f_time.setMinimumWidth(104)
        for t, d in SPANS:
            self.f_time.addItem(t, d)
        self.f_time.setToolTip("依檔案的修改時間篩選")
        row2.addWidget(self.f_time)
        row2.addSpacing(10)

        lab("排序")
        self.f_sort = QtWidgets.QComboBox()
        self.f_sort.setMinimumWidth(104)
        self.f_sort.addItems(SORTS)
        self.f_sort.setToolTip("「相關度」是預設的分數排序;"
                               "只有名稱的那一區沒有分數,照路徑排")
        row2.addWidget(self.f_sort)

        for c in (self.f_kind, self.f_time, self.f_sort):
            c.currentIndexChanged.connect(self._refilter)

        row2.addStretch(1)
        self.fcount = QtWidgets.QLabel("", objectName="hint")
        row2.addWidget(self.fcount)
        row2.addSpacing(12)
        row2.addWidget(self.btn_idx)
        v.addLayout(row2)
        return box

    def _split(self):
        sp = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        sp.setChildrenCollapsible(False)
        sp.addWidget(self._results())
        sp.addWidget(self._detail())
        sp.setSizes([360, 400])
        return sp

    def _results(self):
        self.views = QtWidgets.QStackedWidget()

        # ---- 列表
        # 欄位:# / 分數 / 命中 / 字數 / 日期 / 檔案
        # 檔案欄的索引用常數,不要散落一堆 4、5 —— 之前插一個欄位就得
        # 全檔找一遍哪個 4 是欄位、哪個 4 是別的東西。
        self.tbl = QtWidgets.QTableWidget(0, len(COLS))
        self._headers(["#", "分數", "命中", "字數", "檔案"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setShowGrid(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setWordWrap(False)
        self.tbl.setItemDelegateForColumn(NAMECOL, TwoLine(self.tbl))
        self.tbl.verticalHeader().setDefaultSectionSize(46)
        h = self.tbl.horizontalHeader()
        # 命中欄放得下「222／203·19　📌」—— 太窄的話尾巴被截掉,
        # 檔名命中的 📌 就看不到了
        for c, w in ((0, 46), (1, 76), (2, 132), (3, 72), (DATECOL, 96)):
            self.tbl.setColumnWidth(c, w)
            h.setSectionResizeMode(c, QtWidgets.QHeaderView.Fixed)
        h.setSectionResizeMode(NAMECOL, QtWidgets.QHeaderView.Stretch)
        self.tbl.itemSelectionChanged.connect(self.on_pick)
        self.tbl.doubleClicked.connect(self.open_file)
        self.views.addWidget(self.tbl)

        # ---- 縮圖牆
        self.wall = QtWidgets.QListWidget()
        self.wall.setViewMode(QtWidgets.QListView.IconMode)
        self.wall.setResizeMode(QtWidgets.QListView.Adjust)
        self.wall.setIconSize(QtCore.QSize(150, 150))
        self.wall.setGridSize(QtCore.QSize(178, 206))
        self.wall.setSpacing(6)
        self.wall.setMovement(QtWidgets.QListView.Static)
        self.wall.setUniformItemSizes(True)
        self.wall.setWordWrap(True)
        self.wall.itemSelectionChanged.connect(self.on_pick)
        self.wall.doubleClicked.connect(self.open_file)
        self.views.addWidget(self.wall)
        return self.views

    def _headers(self, labels):
        """
        換欄位標題(文件模式 / 圖片模式用不同的欄位意義)。

        setHorizontalHeaderLabels 會把標題項目整個重建,所以「檔案欄
        置左」這件事每次換標題都要重套一次 —— 只在建介面時設一次,
        第一次搜尋完就被蓋掉了。
        """
        self.tbl.setHorizontalHeaderLabels(labels)
        it = self.tbl.horizontalHeaderItem(len(labels) - 1)
        if it is not None:
            it.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        # 第 4 欄文件模式放「11K」、圖片模式放「2016x3024」,寬度差很多
        self.tbl.setColumnWidth(3, 96 if labels[3] == "尺寸" else 72)

    def _detail(self):
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        self.crumb = QtWidgets.QLabel("點一下結果看前後文,雙擊開啟檔案",
                                      objectName="crumb")
        self.crumb.setStyleSheet("color:%s; font-weight:normal;" % MUTED)
        self.crumb.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        bar.addWidget(self.crumb, 1)
        for txt, fn, tip in (
                ("開啟檔案", self.open_file, "Enter"),
                ("開啟所在資料夾", self.open_dir, ""),
                ("複製路徑", self.copy_path, "Ctrl+C")):
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(fn)
            if tip:
                b.setToolTip(tip)
            bar.addWidget(b)
        v.addLayout(bar)

        self.pane = QtWidgets.QStackedWidget()

        self.txt = QtWidgets.QTextBrowser()
        self.txt.setOpenExternalLinks(False)
        self.txt.document().setDefaultStyleSheet(
            "body{line-height:160%%;}"
            "mark{background:%s;}"
            "ol{margin-left:0px;} li{margin-bottom:9px;}"
            ".warn{color:%s;}" % (HILITE, OCRCLR))
        self.pane.addWidget(self.txt)

        pair = QtWidgets.QWidget(objectName="pane")
        pv = QtWidgets.QVBoxLayout(pair)
        pv.setContentsMargins(12, 10, 12, 12)
        pv.setSpacing(8)
        self.pair_head = QtWidgets.QLabel("")
        self.pair_head.setStyleSheet("color:%s; font-size:10pt;" % MUTED)
        self.pair_head.setWordWrap(True)
        pv.addWidget(self.pair_head)
        ph = QtWidgets.QHBoxLayout()
        ph.setSpacing(12)
        self.pv_q, self.pv_f = PhotoView(), PhotoView()
        for cap, view in (("你拍的 / 選的圖", self.pv_q),
                          ("索引裡找到的圖", self.pv_f)):
            col = QtWidgets.QVBoxLayout()
            col.setSpacing(5)
            lb = QtWidgets.QLabel(cap)
            lb.setAlignment(QtCore.Qt.AlignCenter)
            lb.setStyleSheet("color:%s; font-size:9pt; font-weight:bold;" % MUTED)
            col.addWidget(lb)
            col.addWidget(view, 1)
            ph.addLayout(col, 1)
        pv.addLayout(ph, 1)
        self.pane.addWidget(pair)
        v.addWidget(self.pane, 1)
        return box

    def _statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status = QtWidgets.QLabel("載入索引…", objectName="status")
        sb.addWidget(self.status, 1)
        self.prog = QtWidgets.QProgressBar()
        self.prog.setFixedWidth(220)
        self.prog.setVisible(False)
        sb.addPermanentWidget(self.prog)

    def _shortcuts(self):
        def key(seq, fn, on=None):
            """
            on=None 是整個視窗的快速鍵;指定 widget 則只在那個 widget
            有焦點時才生效。

            這個區別很要緊:Qt 的快速鍵優先於按鍵事件,所以如果把
            Return 掛在視窗上,在搜尋框裡按 Enter 會變成「開啟檔案」
            而不是搜尋。Ctrl+C 同理,掛在視窗上就搶掉前後文面板的複製。
            """
            s = QtGui.QShortcut(QtGui.QKeySequence(seq), on or self)
            if on is not None:
                s.setContext(QtCore.Qt.WidgetShortcut)
            s.activated.connect(fn)

        key("Ctrl+F", lambda: (self.kw.setFocus(), self.kw.selectAll()))
        key("Escape", lambda: (self.kw.setFocus(), self.kw.selectAll()))
        key("Ctrl+V", self.paste_image)
        key("Ctrl+Q", self.close)
        for w in (self.tbl, self.wall):
            key("Return", self.open_file, on=w)
            key("Enter", self.open_file, on=w)
            key("Ctrl+C", self.copy_path, on=w)

    # ------------------------------------------------------------ 資料

    def load_db(self):
        if not os.path.exists(findpile.DB_PATH):
            QtWidgets.QMessageBox.critical(
                self, "找不到索引",
                "還沒有建立索引。\n\n請先在這個資料夾開啟命令列,執行:\n\n"
                "    python findpile.py index")
            QtCore.QTimer.singleShot(0, self.close)
            return
        try:
            con = findpile.db_open()
            n, ch = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(nchars),0) FROM docs").fetchone()
            ocr = con.execute(
                "SELECT COUNT(*) FROM docs WHERE src LIKE 'ocr%'").fetchone()[0]
            nameonly = con.execute(
                "SELECT COUNT(*) FROM docs WHERE src='name'").fetchone()[0]
            self.droots = findpile.display_roots(con)
            con.close()
        except Exception as e:                      # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "索引讀取失敗", str(e))
            QtCore.QTimer.singleShot(0, self.close)
            return

        src = ""
        if len(self.droots) > 1:
            src = "　·　來源 %d 處:%s" % (
                len(self.droots),
                "、".join(findpile.split_display(r.rstrip("\\/"))[1] or r
                          for r in self.droots))
        here = findpile.CONFIG.get("files_here", True)
        self.set_status("索引:%s 個檔案、%.0f 萬字(OCR 辨識 %s 個"
                        "、只有名稱 %s 個)%s%s"
                        % (format(n, ","), ch / 10000, format(ocr, ","),
                           format(nameonly, ","), src,
                           "" if here else "　·　部分原始檔不在本機"))
        self.kw.setFocus()
        self.check_stale(force=True)

    def set_status(self, s):
        self.status.setText(s)

    def busy(self, on):
        self.btn_go.setEnabled(not on)
        self.btn_img.setEnabled(not on)
        self.btn_idx.setEnabled(not on)
        if not on:
            self.prog.setVisible(False)

    def check_stale(self, force=False):
        """
        背景比對磁碟和索引,算出有幾個檔案沒收進來。

        不用 _run():那條路會在結束時呼叫 busy(False),把搜尋按鈕打開 ——
        如果使用者正在搜尋,按鈕就會在搜尋還沒回來之前先亮起來。
        這個檢查是背景雜務,不該碰介面的忙碌狀態。

        60 秒內不重複查。視窗切來切去會發很多次 WindowActivate,
        每次都掃一遍磁碟並不划算。
        """
        if self._stale_t is not None and self._stale_t.isRunning():
            return
        if not force and time.time() - self._stale_at < 60:
            return
        self._stale_at = time.time()

        def work(_t):
            con = findpile.db_open()
            roots = [r for r in findpile.roots_get(con) if os.path.isdir(r)]
            known = {r[0]: (r[1], r[2]) for r in
                     con.execute("SELECT path, mtime, size FROM docs")}
            con.close()
            n = 0
            for root in roots:
                for p, mt, sz in findpile.walk(root):
                    o = known.get(p)
                    if o is None or abs(o[0] - mt) > 1 or o[1] != sz:
                        n += 1
            return n

        t = Task(work, self)
        t.done.connect(self._stale_done)
        t.failed.connect(lambda _m: None)      # 查不到就算了,不要跳錯誤給人看
        self._stale_t = t
        t.start()

    def _stale_done(self, n):
        self._nstale = n
        self.btn_idx.setText("⟳ 更新索引 (%s)" % format(n, ",") if n
                             else "⟳ 更新索引")
        self.btn_idx.setProperty("stale", bool(n))
        # setProperty 不會自己重繪,要踢一下樣式表
        self.btn_idx.style().unpolish(self.btn_idx)
        self.btn_idx.style().polish(self.btn_idx)
        self.btn_idx.setToolTip(
            "有 %s 個檔案新增或修改過,還沒收進索引 —— 現在搜不到它們。\n"
            "按一下重新掃描。" % format(n, ",") if n else
            "重新掃描索引收錄過的資料夾。索引是一份快照,不會自己更新。")

    def event(self, e):
        # 視窗重新取得焦點時查一次:存完檔切回來,馬上就知道要不要更新。
        # 今天的實際情形是「存了檔、搜不到、以為程式壞了」,就是少了這一下。
        if e.type() == QtCore.QEvent.WindowActivate:
            self.check_stale()
        return super().event(e)

    def reindex(self):
        """
        背景重掃索引。

        掃哪些資料夾?用索引裡記錄的 roots,不是設定檔的 scan_root ——
        「更新」的語意是「把已經收錄的範圍再看一次」。這台的索引可能是用
        --root 指到別處建的,拿 scan_root 去掃會掃到不相干的地方,而且
        原本收錄的那些會因為這次沒掃到而被動到。

        cmd_index 是印到 stdout 的命令列函式。與其為了介面改寫它,
        不如把 stdout 接走 —— 它印的每一行本來就是給人看的進度,
        直接送到狀態列剛好。
        """
        import argparse
        import io as _io

        con = findpile.db_open()
        roots = [r for r in findpile.roots_get(con) if os.path.isdir(r)]
        con.close()
        if not roots:
            roots = [findpile.DEFAULT_ROOT]

        names = "、".join(findpile.split_display(r.rstrip("/" + os.sep))[1] or r
                          for r in roots)
        if self._nstale == 0 and QtWidgets.QMessageBox.question(
                self, "更新索引",
                "剛才檢查過,沒有新增或修改過的檔案。\n還是要重新掃描一次嗎?",
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel
        ) != QtWidgets.QMessageBox.Ok:
            return
        if self._nstale and QtWidgets.QMessageBox.question(
                self, "更新索引",
                "有 %s 個檔案新增或修改過,要收進索引嗎?\n\n%s\n\n"
                "期間視窗可以繼續用。"
                % (format(self._nstale, ","), "\n".join(roots)),
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel
        ) != QtWidgets.QMessageBox.Ok:
            return

        self.busy(True)
        self.prog.setVisible(True)
        self.prog.setRange(0, 0)             # 總量未知,先跑馬燈
        self.set_status("更新索引中:%s" % names)

        class _Pipe(_io.TextIOBase):
            """把 cmd_index 印出來的每一行轉成 signal。"""

            def __init__(self, t):
                self.t = t
                self.buf = ""

            def write(self, x):
                self.buf += x
                while "\n" in self.buf:
                    line, self.buf = self.buf.split("\n", 1)
                    if line.strip():
                        self.t.line.emit(line.strip())
                return len(x)

        def work(t):
            a = argparse.Namespace(root=findpile.DEFAULT_ROOT, dir=roots, mac=False,
                                   force=False, full=False, all=False,
                                   scope=None, quiet=False, workers=4)
            old = sys.stdout
            sys.stdout = _Pipe(t)
            try:
                findpile.cmd_index(a)
            finally:
                sys.stdout = old
            return True

        t = self._run(work, self._reindexed)
        t.line.connect(lambda s: self.set_status("更新索引:%s" % s))

    def _reindexed(self, _ok):
        self.prog.setVisible(False)
        self.load_db()                       # 狀態列的筆數要跟著變
        self.check_stale(force=True)         # 數字要歸零,不然按鈕一直掛著
        if self.kws and not self.img_mode:
            # 剛收進來的檔案要立刻找得到,不然使用者會以為沒更新到
            self.go()
        else:
            self.set_status(self.status.text() + "　·　索引已更新")

    # ------------------------------------------------------- 關鍵字搜尋

    def go(self):
        kws = [k for k in self.kw.text().split() if k]
        if not kws:
            return
        mode = "all" if self.m_all.isChecked() else "any"
        loose = self.loose.isChecked()
        self._gen += 1
        gen = self._gen
        self.clear_results()
        self.set_status("搜尋中…")
        self.busy(True)

        def work(_t):
            con = findpile.db_open()          # 每個執行緒要自己的連線
            try:
                return findpile.search(kws, mode, con, loose=loose)
            finally:
                con.close()                  # 不關的話每搜一次洩漏一條

        self._run(work, lambda res: self._show(gen, kws, mode, loose, *res))

    def _redo(self):
        """條件改了就重搜。沒搜過、或正在看圖片結果時不要亂跑。"""
        if self.kw.text().strip() and not self.img_mode and self.rows:
            self.go()

    def _run(self, work, ok):
        t = Task(work, self)
        t.done.connect(ok)
        t.failed.connect(self._failed)
        t.finished.connect(lambda: self.busy(False))
        self._task = t                       # 不留參照會被 GC,執行緒直接死掉
        t.start()
        return t

    def _failed(self, msg):
        self.set_status("失敗:%s" % msg)
        QtWidgets.QMessageBox.critical(self, "搜尋失敗", msg)

    def _show(self, gen, kws, mode, loose, rows, el):
        if gen != self._gen:
            return                           # 已經有更新的搜尋了
        self.img_mode = False
        self.kws = kws
        self.docrows = [r for r in rows if not r["nameonly"]]
        self.namerows = sorted((r for r in rows if r["nameonly"]),
                               key=lambda r: r["path"])
        self._last = (mode, loose, el)
        self.sc_name.setEnabled(bool(self.namerows))
        self.sc_name.setText("🏷 只有名稱 %s" % format(len(self.namerows), ",")
                             if self.namerows else "🏷 只有名稱")
        self.sc_name.setToolTip(
            "影片、音檔、還沒 OCR 的圖片:只能用檔名和資料夾名稱找"
            if self.namerows else "這次沒有只靠名稱命中的檔案")
        # 文件沒命中、名稱有命中,就直接切到名稱區 —— 不然畫面是空的,
        # 看起來像「查不到」,其實旁邊按鈕裡有幾百筆。
        self._rebuild_kinds()
        self.set_scope(1 if not self.docrows and self.namerows else 0)

    def _refilter(self):
        """三個下拉選單共用。只重排現有結果,不重查資料庫。"""
        if self.docrows or self.namerows:
            self.set_scope(self.scope)

    def _rebuild_kinds(self):
        """
        把類型選單換成「這批結果裡真的有的類型」,並帶上筆數。

        固定列出全部八個類型沒有用 —— 查一個詞得到 2,124 筆,人想知道的是
        「裡面有 693 個 PDF」,不是「有沒有簡報這個選項」。

        換內容會觸發 currentIndexChanged,所以整段要擋訊號,
        不然每搜尋一次就會多跑一輪篩選(而且是半成品狀態)。
        """
        cur = self.f_kind.currentText().split(" ")[0]
        counts = {}
        for r in self.docrows + self.namerows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        self.f_kind.blockSignals(True)
        try:
            self.f_kind.clear()
            total = len(self.docrows) + len(self.namerows)
            self.f_kind.addItem("全部 (%s)" % format(total, ","), None)
            for name in findpile.KIND_NAMES + ["其他"]:
                if counts.get(name):
                    self.f_kind.addItem("%s (%s)" % (name, format(counts[name], ",")),
                                        name)
            # 盡量留在使用者原本選的那個類型上
            for i in range(self.f_kind.count()):
                if self.f_kind.itemData(i) == (cur if cur != "全部" else None):
                    self.f_kind.setCurrentIndex(i)
                    break
        finally:
            self.f_kind.blockSignals(False)

    def _filtered(self, rows):
        kind = self.f_kind.currentData()
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        days = self.f_time.currentData()
        if days:
            cut = time.time() - days * 86400
            rows = [r for r in rows if (r.get("mtime") or 0) >= cut]
        i = self.f_sort.currentIndex()
        if i == 1:
            rows = sorted(rows, key=lambda r: -(r.get("mtime") or 0))
        elif i == 2:
            rows = sorted(rows, key=lambda r: (r.get("mtime") or 0))
        return rows

    def set_scope(self, which):
        """0 = 有內文的文件,1 = 只有名稱的檔案(影片、音檔、未 OCR 的圖)。"""
        if which == 1 and not self.sc_name.isEnabled():
            which = 0
        (self.sc_doc if which == 0 else self.sc_name).setChecked(True)
        self.scope = which
        src = self._filtered(self.namerows if which else self.docrows)
        self.nshown = len(src)
        self.rows = src[:300]
        self._headers(list(COLS) if which == 0
                      else ["#", "類型", "命中位置", "大小", "日期", "檔案"])
        self._fill_table()
        nimg = sum(1 for r in self.rows if is_img(r["path"]))
        self.v_grid.setEnabled(nimg > 0)
        self.v_grid.setToolTip("結果裡有 %d 個圖片檔" % nimg if nimg
                               else "這批結果沒有圖片檔")
        mode, loose, el = self._last
        kind = "只有名稱" if which else "有內文"
        self.set_status(
            "「%s」(%s%s)　有內文 %s 個、只有名稱 %s 個　現在看「%s」前 %d 名"
            "　耗時 %.2f 秒%s"
            % ("、".join(self.kws), "全部出現" if mode == "all" else "任一出現",
               "+模糊" if loose else "", format(len(self.docrows), ","),
               format(len(self.namerows), ","), kind, len(self.rows), el,
               "　·　其中 %d 個是圖片,可切「▦ 縮圖」" % nimg if nimg else ""))
        # 篩掉了多少要講出來。只改變畫面卻不說明,看起來就像「結果變少了」
        n_all = len(self.namerows if which else self.docrows)
        self.fcount.setText("" if self.nshown == n_all
                            else "篩選後 %s／%s 筆"
                                 % (format(self.nshown, ","), format(n_all, ",")))
        self.set_view(1 if which == 1 and nimg == len(self.rows) and nimg else 0)
        self.select_first()

    def _fill_table(self):
        # 填表期間擋掉訊號:每加一列都會發 itemSelectionChanged,
        # 300 列就白跑 300 次 on_pick(而且中途的狀態是半成品)
        self.tbl.blockSignals(True)
        try:
            self._fill_table_rows()
        finally:
            self.tbl.blockSignals(False)

    def _fill_table_rows(self):
        self.tbl.setRowCount(0)
        self.tbl.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            folder, name = self._name_of(r["path"])
            if self.img_mode:
                nums = ("%d" % (i + 1), "%.0f%%" % r["score"], r["detail"],
                        "%dx%d" % (r["w"], r["h"]))
                badge = (("✓ 相符", "#dcefe3", OK) if r["sure"]
                         else ("? 要看一眼", "#f6ecd5", OCRCLR))
            elif r.get("nameonly"):
                # 這一區沒有內文,「命中次數」和「字數」都沒有意義。
                # 改成顯示檔案類型、命中在檔名還是資料夾、檔案多大。
                nums = ("%d" % (i + 1),
                        os.path.splitext(r["path"])[1].lstrip(".").upper(),
                        "📌 檔名" if r["namehit"] else "📁 資料夾",
                        findpile.fmt_size(r["size"]))
                badge = ("只有名稱", "#e8eaf2", MUTED)
            else:
                hits = (str(r["total"]) if len(self.kws) == 1
                        else "%d／%s" % (r["total"],
                                        "·".join(str(v) for v
                                                 in r["counts"].values())))
                nums = ("%d" % (i + 1), "%.1f" % r["score"],
                        hits + ("　📌" if r["namehit"] else ""),
                        "%.0fK" % r["kb"])
                badge = (("OCR", "#f6ecd5", OCRCLR)
                         if r["src"].startswith("ocr") else "")
            right = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
            for c, s in enumerate(nums):
                it = QtWidgets.QTableWidgetItem(s)
                it.setTextAlignment(QtCore.Qt.AlignCenter if c == 0 else right)
                if c == 0:
                    it.setForeground(QtGui.QColor(MUTED))
                self.tbl.setItem(i, c, it)
            mt = r.get("mtime") or 0
            d = QtWidgets.QTableWidgetItem(
                time.strftime("%Y-%m-%d", time.localtime(mt)) if mt else "")
            d.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            d.setForeground(QtGui.QColor(MUTED))
            self.tbl.setItem(i, DATECOL, d)

            it = QtWidgets.QTableWidgetItem(name)
            it.setData(FOLDER, folder)
            it.setData(BADGE, badge)
            it.setData(ROWIDX, i)
            it.setToolTip(r["path"])
            self.tbl.setItem(i, NAMECOL, it)

    def _name_of(self, path):
        """路徑拆成 (資料夾, 檔名),多來源時前面加上來源標籤。"""
        folder, name = findpile.split_display(
            findpile.strip_root_any(path, self.droots))
        if len(self.droots) > 1:
            lab = findpile.root_label(path, self.droots)
            folder = ("%s ▸ %s" % (lab, folder)) if folder else lab
        return folder, name

    def clear_results(self):
        self.rows = []
        self.docrows = []
        self.namerows = []
        self.sc_name.setEnabled(False)
        self.sc_name.setText("🏷 只有名稱")
        self.sc_doc.setChecked(True)
        self.scope = 0
        self.tbl.setRowCount(0)
        self.wall.clear()
        self.txt.clear()
        self._thumb_gen += 1
        self.crumb.setText("")

    def select_first(self):
        if self.views.currentIndex() == 1:
            if self.wall.count():
                self.wall.setCurrentRow(0)
                self.wall.setFocus()
        elif self.tbl.rowCount():
            self.tbl.setCurrentCell(0, NAMECOL)
            self.tbl.setFocus()

    # ------------------------------------------------------- 列表 / 縮圖

    def set_view(self, which):
        if which == 1 and not self.v_grid.isEnabled():
            which = 0
        (self.v_list if which == 0 else self.v_grid).setChecked(True)
        self.views.setCurrentIndex(which)
        if which == 1:
            self._fill_wall()
        self.select_first()

    def _fill_wall(self):
        """縮圖牆。只放圖片檔;縮圖在背景讀,先放灰底佔位。"""
        self.wall.blockSignals(True)
        try:
            self._fill_wall_items()
        finally:
            self.wall.blockSignals(False)

    def _fill_wall_items(self):
        self.wall.clear()
        self._thumb_gen += 1
        gen = self._thumb_gen
        ph = QtGui.QPixmap(150, 150)
        ph.fill(QtGui.QColor("#eeece6"))
        shown = 0
        for i, r in enumerate(self.rows):
            if not is_img(r["path"]):
                continue
            _folder, name = self._name_of(r["path"])
            if self.img_mode:
                cap = "%s\n%s %s" % (name[:28],
                                     "✓" if r["sure"] else "?", r["detail"])
            elif r.get("nameonly"):
                # 這一區沒有內文,寫「命中 0」只會讓人以為搜尋壞了
                cap = "%s\n%s · %s" % (name[:28],
                                       "檔名" if r["namehit"] else "資料夾",
                                       findpile.fmt_size(r["size"]))
            else:
                cap = "%s\n命中 %d" % (name[:28], r["total"])
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(ph), cap)
            it.setTextAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
            it.setToolTip(r["path"])
            it.setData(ROWIDX, i)
            if self.img_mode and not r["sure"]:
                it.setForeground(QtGui.QColor(OCRCLR))
            self.wall.addItem(it)
            self._pool.start(ThumbJob(self.wall.count() - 1, gen,
                                      r["path"], 150, self._tsig))
            shown += 1
        if not shown:
            self.wall.addItem(QtWidgets.QListWidgetItem("(這批結果裡沒有圖片檔)"))

    def _thumb_ready(self, row, gen, img):
        """工作執行緒送回 QImage,在這裡(主執行緒)才轉成 QPixmap。"""
        if gen != self._thumb_gen or img is None:
            return
        it = self.wall.item(row)
        if it is not None:
            pm = square(QtGui.QPixmap.fromImage(img), 150)
            if pm is not None:
                it.setIcon(QtGui.QIcon(pm))

    # -------------------------------------------------------- 以圖搜圖

    def go_image(self):
        if not self._img_ready():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "選一張照片", "",
            "圖片 (*.jpg *.jpeg *.png *.heic *.heif *.bmp *.tif *.tiff *.webp);;"
            "所有檔案 (*)")
        if path:
            self.run_image(path)

    def _img_ready(self):
        """以圖搜圖的三個前提:套件、特徵庫、索引。缺哪個就說缺哪個。"""
        if imgfind is None:
            QtWidgets.QMessageBox.critical(
                self, "以圖搜圖無法使用",
                "缺少 opencv-python。\n\n請執行:\n\n"
                "    python -m pip install --user opencv-python\n\n"
                "(原始錯誤:%s)" % IMG_ERR)
            return False
        if imgfind.stats(findpile.DATA_DIR) is None:
            QtWidgets.QMessageBox.information(
                self, "還沒建立圖片特徵",
                "以圖搜圖要先建立一次圖片特徵(約 15 分鐘,之後不必再跑)。\n\n"
                "請在這個資料夾開啟命令列,執行:\n\n"
                "    python findpile.py imgindex")
            return False
        return True

    def paste_image(self):
        """Ctrl+V:剪貼簿裡有圖就直接拿來搜。"""
        if self.kw.hasFocus():
            self.kw.paste()
            return
        cb = QtGui.QGuiApplication.clipboard()
        md = cb.mimeData()
        if md is None:                   # 剪貼簿空的時候真的會回 None
            self.set_status("剪貼簿裡沒有圖片")
            return
        if md.hasImage():
            img = cb.image()
            if not img.isNull():
                fd, p = tempfile.mkstemp(suffix=".png", prefix="findpile_q_")
                os.close(fd)
                img.save(p, "PNG")
                self.run_image(p)
                return
        if md.hasUrls():
            for u in md.urls():
                p = u.toLocalFile()
                if p and is_img(p):
                    self.run_image(p)
                    return
        self.set_status("剪貼簿裡沒有圖片")

    # 拖放:Tkinter 要外掛 tkdnd 才做得到,Qt 內建
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() and any(is_img(u.toLocalFile()) for u in md.urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if is_img(p):
                e.acceptProposedAction()
                self.run_image(p)
                return

    def run_image(self, path):
        if not self._img_ready():
            return
        self._gen += 1
        gen = self._gen
        self.query_path = path
        self.clear_results()
        self.set_status("以圖搜圖:%s …" % os.path.basename(path))
        self.busy(True)
        self.prog.setVisible(True)
        self.prog.setRange(0, 0)             # 粗篩階段長度未知,先跑馬燈

        def work(t):
            def prog(done, total, el, left):
                t.tick.emit(done, total, int(el), int(left))
            return imgfind.search(path, findpile.DATA_DIR, progress=prog)

        t = self._run(work, lambda res: self._show_img(gen, *res))
        t.tick.connect(self._img_tick)

    def _img_tick(self, done, total, el, left):
        self.prog.setRange(0, total)
        self.prog.setValue(done)
        self.set_status("深度搜尋中 %s/%s　已 %d 秒,約剩 %d 秒"
                        % (format(done, ","), format(total, ","), el, left))

    def _show_img(self, gen, rows, note):
        if gen != self._gen:
            return
        self.img_mode = True
        self.rows = rows
        self.docrows, self.namerows = rows, []
        self.nshown = len(rows)
        self.fcount.setText("")
        self.kws = []
        self.sc_doc.setChecked(True)
        self.sc_name.setEnabled(False)
        self.sc_name.setText("🏷 只有名稱")
        self._headers(["#", "相似度", "吻合度", "尺寸", "日期", "檔案"])
        self._fill_table()
        self.v_grid.setEnabled(bool(rows))
        self.v_grid.setToolTip("用縮圖牆一眼掃過全部結果")
        self.set_status("%s　(✓=確定相符　?=可能相關,請自己看一眼)" % note)
        if rows:
            self.set_view(1)                 # 圖片結果預設就給縮圖牆
            self.select_first()
            return
        self.pane.setCurrentIndex(0)
        self.txt.setHtml(
            "<p style='color:%s'>找不到相似的圖片。</p>"
            "<p>可能的原因:</p><ul>"
            "<li>這張圖不在索引範圍裡</li>"
            "<li>拍得太歪,或只拍到一小部分</li>"
            "<li>圖片特徵還沒建完(<code>python findpile.py imgindex</code>)</li>"
            "</ul>" % MUTED)

    # ------------------------------------------------------------ 細節

    def cur(self):
        """
        目前選到哪一筆。

        一定要查邊界:換結果的時候 self.rows 已經換新了,但舊的縮圖牆
        項目還在,而 Qt 清除項目時會把「正在被刪掉的那個」當成當前選取
        發出 itemSelectionChanged。那些項目的 ROWIDX 還指向舊清單的
        第 200 筆,拿去索引只有 2 筆的新清單就炸了。
        """
        if self.views.currentIndex() == 1:
            it = self.wall.currentItem()
            i = it.data(ROWIDX) if it is not None else None
        else:
            i = self.tbl.currentRow()
        if i is None or not isinstance(i, int) or not 0 <= i < len(self.rows):
            return None
        return self.rows[i]

    def on_pick(self):
        r = self.cur()
        if not r:
            return
        self.crumb.setText(findpile.strip_root_any(r["path"], self.droots))
        self.crumb.setStyleSheet("")
        if self.img_mode:
            self._show_pair(r)
        else:
            self._show_text(r)

    def _show_text(self, r):
        self.pane.setCurrentIndex(0)
        out = []
        if r["src"].startswith("ocr"):
            out.append("<p class='warn'>⚠ 這份是 OCR 辨識的掃描檔,可能有錯字。"
                       "要引用內容請開啟原始檔核對。</p>")
        if r.get("nameonly"):
            where = "檔名" if r["namehit"] else "資料夾名稱"
            out.append("<p style='color:%s'>這個檔案抽不出內文(影片、音檔,"
                       "或還沒 OCR 的圖片),是靠<b>%s</b>找到的。<br>"
                       "大小 %s　·　按 Enter 用系統預設程式開啟。</p>"
                       % (MUTED, where, findpile.fmt_size(r["size"])))
            self.txt.setHtml("".join(out))
            return
        segs = findpile.snippets(r["text"], self.kws, 12, 220) if self.kws else []
        if not segs:
            out.append("<p style='color:%s'>(只有檔名命中,內文沒有這個關鍵字)</p>"
                       % MUTED)
        else:
            pat = re.compile("|".join(re.escape(k) for k in self.kws))
            out.append("<ol>")
            for s in segs:
                # 先 escape 再插 <mark>,否則內文裡的 < 會被當成標籤
                esc = html.escape(s)
                out.append("<li>%s</li>" % pat.sub(
                    lambda m: "<mark>%s</mark>" % m.group(), esc))
            out.append("</ol>")
        self.txt.setHtml("".join(out))

    def _show_pair(self, r):
        self.pane.setCurrentIndex(1)
        bits = ["%s　相似度 %.0f%%" % (r["how"], r["score"])]
        if r["dist"] is not None:
            bits.append("pHash 差異 %d／64" % r["dist"])
        if r["inliers"] is not None:
            bits.append("幾何吻合點 %d" % r["inliers"])
        bits.append("✓ 確定相符" if r["sure"]
                    else "? 只是版面接近,沒通過幾何驗證 —— 請自己看一眼")
        self.pair_head.setText("　·　".join(bits))
        self.pv_q.set_path(self.query_path)
        self.pv_f.set_path(r["path"])

    # ------------------------------------------------------------ 動作

    def _not_here(self, path):
        """
        只複製了索引、沒有原始檔的情形。丟一句錯誤沒有用 ——
        使用者要知道檔案在哪台機器的哪裡,所以順手把路徑放進剪貼簿。
        """
        QtGui.QGuiApplication.clipboard().setText(path)
        QtWidgets.QMessageBox.information(
            self, "原始檔不在這台電腦",
            "這台只有索引,沒有檔案本身。\n\n完整路徑(已複製到剪貼簿):\n\n%s\n\n"
            "回到原本那台電腦貼上路徑就能開啟。\n"
            "若已經把資料複製到這台,請編輯 config.json 的 path_map。" % path)

    def open_file(self):
        r = self.cur()
        if not r:
            return
        try:
            findpile.open_path(r["path"])
        except findpile.NotHere:
            self._not_here(r["path"])
        except Exception as e:                      # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "無法開啟", "%s\n\n%s" % (r["path"], e))

    def open_dir(self):
        r = self.cur()
        if not r:
            return
        try:
            findpile.reveal_path(r["path"])
        except findpile.NotHere:
            self._not_here(r["path"])
        except Exception as e:                      # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "無法開啟", str(e))

    def copy_path(self):
        r = self.cur()
        if not r:
            return
        QtGui.QGuiApplication.clipboard().setText(r["path"])
        self.set_status("已複製路徑:%s" % r["path"])


def main():
    app = QtWidgets.QApplication(sys.argv)
    # Fusion 是 Qt 自己畫的樣式。不指定的話 Windows 會用原生樣式,
    # QSS 裡的圓角、padding 有一半會被原生繪製蓋掉。
    app.setStyle("Fusion")
    f = QtGui.QFont(pick_font())
    f.setPointSizeF(10.5)
    app.setFont(f)
    app.setStyleSheet(QSS)
    w = Win()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
