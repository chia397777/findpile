#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_app.py —— 新介面(app.py)的離屏煙霧測試。

    python test_app.py              關鍵字從索引裡自動挑
    python test_app.py 報價          指定關鍵字

需要索引已建好(它會拿真的資料跑,不用假資料 —— 假資料驗不出
「真的檔案長得很怪」這類問題)。圖片特徵庫可有可無,沒有的話
第 7~9 段自動跳過。

為什麼用 offscreen 平台:不開真視窗,但畫面照樣會渲染,所以
widget.grab() 可以把自訂的兩行繪製、縮圖縮放逼出來真的執行過 ——
不是只驗「物件建起來了」。

注意:Windows 的 offscreen 平台不列舉系統字型,pick_font() 在那裡只會
拿到 "Sans Serif",要檢查中文字型得用真平台(可搭配 WA_DontShowOnScreen,
完整跑版面但不彈出視窗)。macOS 的 offscreen 走 CoreText,字型照樣列得出來
—— 2026-10-26 在 macOS 26.5.2 上實測印出 PingFang TC。
"""
import os
import tempfile
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6 import QtCore, QtGui, QtWidgets    # noqa: E402

import app as A                                 # noqa: E402
import imgfind                                  # noqa: E402
import findpile                                  # noqa: E402

ok, bad = [], []


def pick_keyword(con):
    """
    從索引裡挑一個「這個索引一定找得到」的關鍵字。

    寫死任何一個詞都不可能通用 —— 每個人索引的是自己的檔案。
    所以從一份中等大小的文件裡切兩個字出來:兩個字是因為
    單字在中文裡太容易撞到切詞邏輯的邊界,而兩字組正是
    search() 內部的比對單位。
    """
    row = con.execute(
        "SELECT text FROM docs WHERE nchars BETWEEN 200 AND 20000"
        " AND text IS NOT NULL ORDER BY nchars LIMIT 1").fetchone()
    if not row:
        return None
    t = row[0]
    # 跳過開頭常見的頁碼、空白、標點,找連續兩個「字」
    for i in range(len(t) - 1):
        a, b = t[i], t[i + 1]
        if a.isalnum() and b.isalnum() and not a.isspace():
            return a + b
    return None


def chk(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("  %s %s%s" % ("✓" if cond else "✗", name,
                         ("　" + str(extra)) if extra else ""))


qapp = QtWidgets.QApplication(sys.argv)
qapp.setStyle("Fusion")
f = QtGui.QFont(A.pick_font())
f.setPointSizeF(10.5)
qapp.setFont(f)
qapp.setStyleSheet(A.QSS)

print("字型:%s" % A.pick_font())
# 沒有索引就不要開窗 —— Win() 會彈一個 modal 對話框要人按「確定」,
# offscreen 平台沒有人可以按,測試會無聲掛死(連失敗訊息都沒有)。
if not os.path.exists(findpile.DB_PATH):
    print("找不到索引:%s" % findpile.DB_PATH)
    print("")
    print("先建索引再跑測試:")
    print("    python findpile.py --root <你的資料夾> index")
    sys.exit(0)

print("")
print("1. 開窗 + 讀索引")
w = A.Win()
w.resize(1240, 800)
w.show()
# 不要自己呼叫 load_db():__init__ 裡有個 60 ms 的 timer 會呼叫它。
# 兩邊都叫的話,timer 那次有時會晚到,把後面搜尋的狀態列蓋回「索引:…」,
# 測試就變成 50% 機率失敗 —— 那是測試的錯,不是程式的錯。
for _ in range(60):
    qapp.processEvents()
    if "索引:" in w.status.text():
        break
    QtCore.QThread.msleep(30)
chk("視窗建起來", w.isVisible())
chk("索引狀態列有內容", "索引:" in w.status.text(), w.status.text()[:46])
chk("QSS 有吃進去", "border-radius" in qapp.styleSheet())
chk("搜尋框拿到焦點", w.kw.hasFocus())

print("")
print("2. 關鍵字搜尋(同步跑,不經執行緒)")
con = findpile.db_open()
KW = sys.argv[1] if len(sys.argv) > 1 else pick_keyword(con)
if not KW:
    con.close()
    print("     索引是空的,沒東西可測。先跑一次:")
    print("         python findpile.py index")
    sys.exit(0)
print("     用的關鍵字:%s" % KW)
rows, el = findpile.search([KW], "any", con)
con.close()
chk("findpile.search 有結果", len(rows) > 0, "%d 筆 / %.2f 秒" % (len(rows), el))
w._show(w._gen, [KW], "any", False, rows, el)
qapp.processEvents()
chk("表格有填", w.tbl.rowCount() > 0, "%d 列" % w.tbl.rowCount())
chk("第 1 欄是序號 1", w.tbl.item(0, 0).text() == "1")
chk("檔案欄帶了資料夾(給 delegate 用)",
    bool(w.tbl.item(0, A.NAMECOL).data(A.FOLDER) is not None))
chk("欄位標題是文件模式", w.tbl.horizontalHeaderItem(1).text() == "分數")
chk("狀態列報了兩區的筆數", "有內文" in w.status.text()
    and "只有名稱" in w.status.text(), w.status.text()[:70])

print("")
print("2b. 只有名稱的檔案(影片、音檔、沒 OCR 的圖)")
# 從索引裡挑一個只會打到 src='name' 的字:拿名稱紀錄的上層資料夾名。
# 寫死關鍵字的話,換一個資料夾跑這個測試就整段跳過了。
con = findpile.db_open()
NAMEKW = None
for (p,) in con.execute("SELECT path FROM docs WHERE src='name' LIMIT 400"):
    cand = os.path.basename(os.path.dirname(p))
    if len(cand) >= 3 and not cand[0].isdigit():
        NAMEKW = cand
        break
if NAMEKW:
    nrows, nel = findpile.search([NAMEKW], "any", con)
    nn = [r for r in nrows if r["nameonly"]]
    print("     關鍵字「%s」:有內文 %d 筆,只有名稱 %d 筆"
          % (NAMEKW, len(nrows) - len(nn), len(nn)))
    w._show(w._gen, [NAMEKW], "any", False, nrows, nel)
    qapp.processEvents()
    chk("有名稱命中時鈕要啟用", w.sc_name.isEnabled())
    chk("鈕上有帶筆數", any(c.isdigit() for c in w.sc_name.text()),
        w.sc_name.text())
    w.set_scope(1)
    qapp.processEvents()
    chk("切到名稱區", w.scope == 1)
    chk("名稱區欄位標題換掉了",
        w.tbl.horizontalHeaderItem(1).text() == "類型",
        w.tbl.horizontalHeaderItem(1).text())
    chk("名稱區每列都是 nameonly",
        all(r["nameonly"] for r in w.rows), "%d 列" % len(w.rows))
    chk("命中位置欄寫了檔名或資料夾",
        w.tbl.item(0, 2).text() in ("📌 檔名", "📁 資料夾"),
        w.tbl.item(0, 2).text())
    chk("大小欄是人看得懂的單位",
        any(u in w.tbl.item(0, 3).text() for u in ("B", "KB", "MB", "GB")),
        w.tbl.item(0, 3).text())
    chk("badge 標了「只有名稱」",
        w.tbl.item(0, A.NAMECOL).data(A.BADGE)[0] == "只有名稱",
        w.tbl.item(0, A.NAMECOL).data(A.BADGE))
    chk("名稱區畫得出來", not w.tbl.grab().isNull())
    w.tbl.setCurrentCell(0, A.NAMECOL)
    qapp.processEvents()
    chk("前後文面板說明抽不出內文", "抽不出內文" in w.txt.toHtml())
    w.set_scope(0)
    qapp.processEvents()
    chk("切回文件區,標題還原",
        w.tbl.horizontalHeaderItem(1).text() == "分數")
else:
    print("     (跳過:索引裡沒有 src='name' 的紀錄,先跑一次 index)")
con.close()
w._show(w._gen, [KW], "any", False, rows, el)
qapp.processEvents()

print("")
print("2c. 類型 / 時間 / 排序")
kinds = {r["kind"] for r in w.docrows}
print("     這批結果的類型:%s" % "、".join(sorted(kinds)))
chk("每筆都有 kind 和 mtime",
    all(r.get("kind") and "mtime" in r for r in w.docrows[:50]))
chk("類型選單只列真的有的類型",
    all(w.f_kind.itemData(i) in kinds or w.f_kind.itemData(i) is None
        for i in range(w.f_kind.count())),
    [w.f_kind.itemText(i) for i in range(w.f_kind.count())])
chk("類型選單帶了筆數", "(" in w.f_kind.itemText(0), w.f_kind.itemText(0))
chk("表格有日期欄", w.tbl.horizontalHeaderItem(A.DATECOL).text() == "日期")
d0 = w.tbl.item(0, A.DATECOL).text()
chk("日期欄是 YYYY-MM-DD", len(d0) == 10 and d0[4] == "-", d0)

pick = None
for i in range(1, w.f_kind.count()):
    if w.f_kind.itemData(i):
        pick = i
        break
if pick:
    want = w.f_kind.itemData(pick)
    before = w.tbl.rowCount()
    w.f_kind.setCurrentIndex(pick)          # 觸發 _refilter
    qapp.processEvents()
    chk("篩類型後每一列都是那個類型",
        all(r["kind"] == want for r in w.rows), "%s / %d 列" % (want, len(w.rows)))
    w.f_kind.setCurrentIndex(0)
    qapp.processEvents()
    chk("切回全部會還原", w.tbl.rowCount() == before,
        "%d -> %d" % (before, w.tbl.rowCount()))

w.f_sort.setCurrentIndex(1)
qapp.processEvents()
mts = [r.get("mtime") or 0 for r in w.rows]
chk("最新在前:時間遞減", mts == sorted(mts, reverse=True), "%d 列" % len(mts))
w.f_sort.setCurrentIndex(2)
qapp.processEvents()
mts = [r.get("mtime") or 0 for r in w.rows]
chk("最舊在前:時間遞增", mts == sorted(mts), "%d 列" % len(mts))
w.f_sort.setCurrentIndex(0)
qapp.processEvents()

import time as _t
w.f_time.setCurrentIndex(1)                 # 最近 7 天
qapp.processEvents()
cut = _t.time() - 7 * 86400
chk("時間篩選真的有濾掉舊的",
    all((r.get("mtime") or 0) >= cut for r in w.rows), "剩 %d 列" % len(w.rows))
w.f_time.setCurrentIndex(0)
qapp.processEvents()
chk("時間切回不限會還原", len(w.rows) > 0, "%d 列" % len(w.rows))

print("")
print("2d. 索引過期提示 / 更新索引按鈕")
chk("有更新索引按鈕", hasattr(w, "btn_idx") and w.btn_idx.isVisible())
# UI 那一半用固定數字測,才不會隨磁碟內容變來變去。
# _auto_done 先關掉 —— 不然 _stale_done(12) 會觸發真的自動更新,
# 在測試中途跑一次完整索引(而且會把下面的輸出攪亂)。
w._auto_done = True
w._stale_done(12)
qapp.processEvents()
chk("過期時按鈕帶數字", "12" in w.btn_idx.text(), w.btn_idx.text())
chk("過期時 stale property 打開", w.btn_idx.property("stale") is True)
chk("過期時滑鼠提示說得清楚", "搜不到" in w.btn_idx.toolTip(),
    w.btn_idx.toolTip()[:40])
chk("過期時按鈕畫得出來", not w.btn_idx.grab().isNull())
w._stale_done(0)
qapp.processEvents()
chk("沒過期就恢復原樣", w.btn_idx.text() == "⟳ 更新索引"
    and not w.btn_idx.property("stale"), w.btn_idx.text())

w._stale_at = 0
w.check_stale(force=True)
t0 = time.time()
while w._stale_t is not None and w._stale_t.isRunning() and time.time() - t0 < 60:
    qapp.processEvents()
    QtCore.QThread.msleep(50)
qapp.processEvents()
chk("背景檢查跑得完", w._stale_t is not None and not w._stale_t.isRunning(),
    "%.1f 秒" % (time.time() - t0))
chk("算出來是個數字", isinstance(w._nstale, int), "%s 個待更新" % w._nstale)
chk("檢查期間不會動到忙碌狀態", w.btn_go.isEnabled())

print("")
print("2e. 開窗自動更新索引")
_calls = []
_real_reindex = w.reindex
w.reindex = lambda auto=False: _calls.append(auto)

w._auto_done = False
w._stale_done(5)
chk("有待更新就自動跑,而且是 auto", _calls == [True], _calls)

_calls.clear()
w._stale_done(3)
chk("同一次開窗只自動跑一次", _calls == [], _calls)

_calls.clear()
w._auto_done = False
w._stale_done(0)
chk("沒東西要更新就完全不跑", _calls == [] and w._auto_done is False, _calls)

_calls.clear()
findpile.CONFIG["auto_index"] = False
w._auto_done = False
w._stale_done(5)
chk("config 關掉 auto_index 就不自動跑", _calls == [], _calls)
findpile.CONFIG.pop("auto_index")

w.reindex = _real_reindex
w._auto_done = True
w._stale_done(0)
chk("按鈕最後回到乾淨狀態", w.btn_idx.text() == "⟳ 更新索引")

print("")
print("2f. 資料保護(沙盒,不碰真索引)")
import argparse as _ap
import io as _io
import shutil as _sh
import tempfile as _tf

_box = _tf.mkdtemp(prefix="lf_guard_")
_img = os.path.join(_box, "收據.jpg")
_pdf = os.path.join(_box, "掃描.pdf")
_io.open(os.path.join(_box, "a.txt"), "w", encoding="utf-8").write("報價單 三萬")
_io.open(_img, "wb").write(os.urandom(90000))
_io.open(_pdf, "wb").write(b"%PDF-1.4 fake")
_real_db = findpile.DB_PATH
findpile.DB_PATH = os.path.join(_box, "idx.db")


def _idx(full=False):
    findpile.cmd_index(_ap.Namespace(root=_box, dir=None, mac=False, force=False,
                                    full=full, all=False, scope=None,
                                    quiet=False, workers=2), say=lambda *a: None)


def _rec(p):
    c = findpile.db_open()
    r = c.execute("SELECT nchars,src FROM docs WHERE path=?", (p,)).fetchone()
    c.close()
    return r


_idx()
_c = findpile.db_open()
for _p in (_img, _pdf):
    _st = os.stat(_p)
    _c.execute(findpile.DOCS_INSERT, (_p, _st.st_mtime, _st.st_size,
                                     os.path.splitext(_p)[1], 58, "",
                                     "收據 金額 12,000", "ocr-rapid"))
_c.commit()
_c.close()
chk("OCR 紀錄灌得進去", _rec(_img) == (58, "ocr-rapid"), _rec(_img))

_t = time.time() + 86400
os.utime(_img, (_t, _t))
os.utime(_pdf, (_t, _t))
_idx()
chk("圖片改動過,index 不覆蓋 OCR", _rec(_img) == (58, "ocr-rapid"), _rec(_img))
chk("PDF 改動過,index 不用空白抽文覆蓋", _rec(_pdf) == (58, "ocr-rapid"), _rec(_pdf))

_idx(full=True)
chk("index --full 不刪 OCR 紀錄",
    _rec(_img) == (58, "ocr-rapid") and _rec(_pdf) == (58, "ocr-rapid"),
    "%s / %s" % (_rec(_img), _rec(_pdf)))

# merge:舊的空白名稱紀錄不可以蓋掉現有的 OCR
_old = os.path.join(_box, "old.db")
_keep, findpile.DB_PATH = findpile.DB_PATH, _old
_c = findpile.db_open()
_st = os.stat(_img)
_c.execute(findpile.DOCS_INSERT, (_img, _st.st_mtime, _st.st_size, ".jpg",
                                 0, "", None, "name"))
_c.commit()
_c.close()
findpile.DB_PATH = _keep
findpile.cmd_merge(_ap.Namespace(db=_old, label=None, as_src=None))
chk("merge 不讓名稱紀錄蓋掉 OCR", _rec(_img) == (58, "ocr-rapid"), _rec(_img))

# 反向:真的刪掉的檔案還是要清掉,保護不能變成「什麼都不刪」
os.remove(_img)
_idx()
chk("真的刪掉的檔案照樣清掉", _rec(_img) is None, _rec(_img))

findpile.DB_PATH = _real_db
_sh.rmtree(_box, ignore_errors=True)

print("")
print("2f2. 搜尋規則的一致性")
_c = findpile.db_open()
_a, _ = findpile.search(["TXT"], "any", _c)
_b, _ = findpile.search(["txt"], "any", _c)
chk("英文關鍵字不分大小寫", len(_a) == len(_b), "%d / %d" % (len(_a), len(_b)))
_one, _ = findpile.search([KW], "any", _c)
_two, _ = findpile.search([KW, KW], "any", _c)
chk("重複關鍵字不會灌水",
    _one and _two and abs(_one[0]["score"] - _two[0]["score"]) < 1e-9,
    "%.3f / %.3f" % (_one[0]["score"], _two[0]["score"]) if _one and _two else "")
_c.close()
chk("SKIP_DIRS 不分大小寫",
    all(findpile._skip_dir(d) for d in ("build", "Build", "BUILD", "NODE_MODULES"))
    and not findpile._skip_dir("文件"))
chk(".gif 收得進索引,縮圖也認得",
    ".gif" in findpile.EXT_IMG and ".gif" in findpile.EXT_NAME
    and A.is_img("a.GIF"))

print("")
print("2g. 並行與關窗防護")
chk("有 closeEvent", hasattr(w, "closeEvent"))
chk("更新索引有擋重入",
    "isRunning" in __import__("inspect").getsource(w.reindex))
chk("busy 不會在別人還在忙時解鎖",
    "isRunning" in __import__("inspect").getsource(w.busy))
chk("索引執行緒會 deleteLater",
    "deleteLater" in __import__("inspect").getsource(w.reindex))
# 根目錄都掛不上時不可以改掃別處 —— 用行為測,不是看原始碼。
# 掃 DEFAULT_ROOT 的後果很嚴重:cmd_index 會把它 roots_add 進索引,
# 以後每次更新都掃那裡;而掛不上的那批路徑會被整批判定成已刪除。
_rg = findpile.roots_get
findpile.roots_get = lambda _c: [os.path.join(os.sep, "這個路徑不存在_qq")]
try:
    w._idx_t = None
    w.reindex(auto=True)                     # auto 才不會彈 modal 對話框
    qapp.processEvents()
    chk("根目錄掛不上就不做,不改掃別處",
        w._idx_t is None, "狀態列:%s" % w.status.text()[:34])
finally:
    findpile.roots_get = _rg

print("")
print("")
print("2h. 真的走一次背景執行緒(前面的測試都繞過 go(),所以漏掉了這個)")
# 這一節存在的理由:busy(False) 曾經在 Task.finished 裡去問
# self._task.isRunning() —— 那一刻執行緒還是 running,所以每次搜尋
# 結束都提早 return,按鈕永遠解不了鎖、狀態列停在「搜尋中…」。
# 直接呼叫 _show() 的測試完全看不到這件事。
w.kw.setText(KW)
w.go()
chk("搜尋期間按鈕會鎖住", not w.btn_go.isEnabled())
_t0 = time.time()
while w._task is not None and w._task.isRunning() and time.time() - _t0 < 60:
    qapp.processEvents()
    QtCore.QThread.msleep(30)
for _ in range(20):                       # 讓 finished / done 的 queued 訊號送達
    qapp.processEvents()
    QtCore.QThread.msleep(20)
chk("搜尋結束後按鈕解鎖", w.btn_go.isEnabled(), "耗時 %.1f 秒" % (time.time() - _t0))
chk("以圖搜圖鈕也解鎖", w.btn_img.isEnabled())
chk("更新索引鈕也解鎖", w.btn_idx.isEnabled())
chk("狀態列不再是「搜尋中」", "搜尋中" not in w.status.text(),
    w.status.text()[:50])
chk("結果有填進表格", w.tbl.rowCount() > 0, "%d 列" % w.tbl.rowCount())
chk("忙碌計數歸零", w._busy_n == 0, w._busy_n)
chk("最後一欄標題是「檔案」",
    w.tbl.horizontalHeaderItem(A.NAMECOL).text() == "檔案",
    w.tbl.horizontalHeaderItem(A.NAMECOL).text())

print("")
print("")
print("3. 強制渲染 —— TwoLine delegate 的 paint() 要真的跑過")
pm = w.tbl.grab()
chk("表格畫得出來", not pm.isNull() and pm.width() > 100,
    "%dx%d" % (pm.width(), pm.height()))
pm2 = w.grab()
chk("整個視窗畫得出來", not pm2.isNull() and pm2.width() > 900,
    "%dx%d" % (pm2.width(), pm2.height()))

print("")
print("4. 點選 -> 前後文面板")
w.tbl.setCurrentCell(0, A.NAMECOL)
qapp.processEvents()
chk("麵包屑有路徑", len(w.crumb.text()) > 3, w.crumb.text()[:50])
h = w.txt.toHtml()
chk("前後文面板切到文字頁", w.pane.currentIndex() == 0)
has_mark = "<mark" in h or KW in h
chk("前後文有內容", has_mark, "長度 %d" % len(h))

print("")
print("5. HTML escape —— 內文有 < 不能變成標籤")
fake = dict(w.rows[0])
fake["text"] = "甲方<乙方>的 %s 權利 & 其他" % KW
fake["src"] = "extract"
w.rows = [fake] + w.rows[1:]
w._show_text(fake)
h = w.txt.toHtml()
chk("< 被 escape 掉了", "&lt;" in h, "有 &lt; 代表沒被當標籤")

print("")
print("6. 縮圖牆(關鍵字結果裡的圖片檔)")
nimg = sum(1 for r in w.rows if A.is_img(r["path"]))
print("     結果裡有 %d 個圖片檔" % nimg)
if nimg:
    chk("縮圖鈕有開", w.v_grid.isEnabled())
    w.set_view(1)
    for _ in range(40):          # 等背景縮圖
        qapp.processEvents()
        QtCore.QThread.msleep(40)
    chk("縮圖牆有項目", w.wall.count() > 0, "%d 格" % w.wall.count())
    icons = sum(1 for i in range(w.wall.count())
                if not w.wall.item(i).icon().isNull())
    chk("縮圖有讀進來", icons > 0, "%d/%d 格有圖" % (icons, w.wall.count()))
    pm3 = w.wall.grab()
    chk("縮圖牆畫得出來", not pm3.isNull())
    w.set_view(0)
else:
    print("     (跳過:這批結果沒有圖片)")

print("")
print("7. 以圖搜圖的顯示路徑(用特徵庫裡真的圖,不跑比對)")
fcon = imgfind.feat_open(findpile.DATA_DIR)
pics = fcon.execute("SELECT path,w,h FROM imgs WHERE nfeat>300 LIMIT 4").fetchall()
fcon.close()
if len(pics) < 2:
    # 特徵庫是 imgindex 建的。搬到另一台、或還沒跑過 imgindex 時它是空的。
    # 第 6 段本來就會判斷「沒圖就跳過」,這裡卻直接拿 pics[0] 用 ——
    # 在 Windows 上特徵庫一直都在,所以這條路從來沒走過,直到搬上 Mac 才炸。
    print("     (跳過 7~9 段:特徵庫裡只有 %d 張圖。要測這幾段,"
          "先跑一次 python findpile.py imgindex)" % len(pics))
else:
    chk("特徵庫讀得到圖", len(pics) >= 2, "%d 張" % len(pics))
    irows = [
        imgfind._mk(pics[0][0], pics[0][1], pics[0][2], 0, 355, True, "深度搜尋", 0.99),
        imgfind._mk(pics[1][0], pics[1][1], pics[1][2], 11, 24, False, "深度搜尋", 0.31),
    ]
    w.query_path = pics[0][0]
    w._show_img(w._gen, irows, "測試:2 張")
    qapp.processEvents()
    chk("切到圖片模式", w.img_mode)
    chk("欄位標題換成相似度", w.tbl.horizontalHeaderItem(1).text() == "相似度")
    chk("圖片結果預設走縮圖牆", w.views.currentIndex() == 1,
        "currentIndex=%d" % w.views.currentIndex())
    for _ in range(40):
        qapp.processEvents()
        QtCore.QThread.msleep(40)
    chk("縮圖牆有 2 格", w.wall.count() == 2, "%d 格" % w.wall.count())
    icons = sum(1 for i in range(w.wall.count())
                if not w.wall.item(i).icon().isNull())
    chk("兩張縮圖都讀到", icons == 2, "%d/2" % icons)

    w.wall.setCurrentRow(0)
    qapp.processEvents()
    chk("面板切到並排比對頁", w.pane.currentIndex() == 1)
    chk("並排的標頭有吻合點數", "幾何吻合點" in w.pair_head.text(),
        w.pair_head.text()[:56])
    chk("左邊(查詢圖)有畫出來", w.pv_q.pixmap() is not None
        and not w.pv_q.pixmap().isNull())
    chk("右邊(找到的圖)有畫出來", w.pv_f.pixmap() is not None
        and not w.pv_f.pixmap().isNull())
    pm4 = w.grab()
    chk("並排頁整頁畫得出來", not pm4.isNull())

    print("")
    print("8. 第二筆是「?」—— 不確定的要標出來")
    w.wall.setCurrentRow(1)
    qapp.processEvents()
    chk("標頭說了沒通過幾何驗證", "沒通過幾何驗證" in w.pair_head.text(),
        w.pair_head.text()[-30:])

    print("")
    print("9. 切回列表模式,欄位標題要還原")
    w.set_view(0)
    pm5 = w.tbl.grab()
    chk("圖片模式的列表也畫得出來", not pm5.isNull())
    chk("✓/? 標籤有存進 BADGE", w.tbl.item(0, A.NAMECOL).data(A.BADGE)[0] == "✓ 相符",
        w.tbl.item(0, A.NAMECOL).data(A.BADGE))
    w._show(w._gen, [KW], "any", False, rows, el)
    chk("切回關鍵字模式後標題還原", w.tbl.horizontalHeaderItem(1).text() == "分數")
    chk("img_mode 關掉了", w.img_mode is False)

print("")
print("10. 世代控制 —— 舊搜尋晚回來不該蓋掉新結果")
w._gen += 1
before = w.tbl.rowCount()
w._show(w._gen - 1, ["舊的"], "any", False, rows[:3], 0.1)
chk("舊世代的結果被丟掉", w.tbl.rowCount() == before,
    "%d 列沒變" % w.tbl.rowCount())

print("")
print("11. 雜項")
w.set_view(0)
w.tbl.setCurrentCell(0, A.NAMECOL)
r = w.cur()
chk("cur() 拿得到選中那筆", r is not None and "path" in r)
w.copy_path()
chk("複製路徑有回報", "已複製路徑" in w.status.text())
QtGui.QGuiApplication.clipboard().clear()
w.kw.clearFocus()
w.paste_image()
chk("空剪貼簿會說沒有圖", "沒有圖片" in w.status.text(), w.status.text())
chk("is_img 認得 .HEIC", A.is_img("a.HEIC") and A.is_img("b.jpg")
    and not A.is_img("c.pdf"))
# 自己產一張圖,不要靠特徵庫 —— 這個檢查不該因為換一台機器就消失。
_png = os.path.join(tempfile.gettempdir(), "findpile_test_thumb.png")
_im = QtGui.QImage(40, 30, QtGui.QImage.Format_RGB888)
_im.fill(QtGui.QColor("#336699"))
_im.save(_png)
chk("read_thumb 讀得到真圖", A.read_thumb(_png, 120) is not None)
os.remove(_png)
chk("read_thumb 對爛路徑回 None", A.read_thumb(os.path.join(os.path.dirname(__file__),
                                             "沒這個檔.jpg"), 120) is None)
w.clear_results()
chk("clear_results 清乾淨", w.tbl.rowCount() == 0 and w.wall.count() == 0)
w._redo()
chk("沒結果時 _redo 不會亂跑", w.tbl.rowCount() == 0)

print("")
print("=" * 58)
print("  %d 項通過,%d 項失敗" % (len(ok), len(bad)))
if bad:
    for b in bad:
        print("    ✗ %s" % b)
    sys.exit(1)
print("  ✓ 全部通過")
