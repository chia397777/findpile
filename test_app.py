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
    bool(w.tbl.item(0, 4).data(A.FOLDER) is not None))
chk("欄位標題是文件模式", w.tbl.horizontalHeaderItem(1).text() == "分數")
chk("狀態列報了筆數", "命中" in w.status.text(), w.status.text()[:60])

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
w.tbl.setCurrentCell(0, 4)
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
    chk("✓/? 標籤有存進 BADGE", w.tbl.item(0, 4).data(A.BADGE)[0] == "✓ 相符",
        w.tbl.item(0, 4).data(A.BADGE))
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
w.tbl.setCurrentCell(0, 4)
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
