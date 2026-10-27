#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gui.py —— findpile 的視窗介面(Windows / macOS 通用)

開啟方式:
  Windows   資料夾裡的 搜尋視窗.bat(或自己拉一個桌面捷徑)
  macOS     資料夾裡的 開啟搜尋.command,或 python3 gui.py

檔名刻意用英文:cmd.exe 讀批次檔是用系統 OEM 編碼,
批次檔裡寫 UTF-8 中文檔名會變亂碼而啟動失敗。

操作:
  1. 在上面的框輸入關鍵字,按 Enter
  2. 下面出現結果,點一下看前後文
  3. 雙擊直接開啟那個檔案
"""

import os
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import findpile                                     # noqa: E402

# 以圖搜圖是附加功能。缺 opencv 或 PIL 不該讓整個搜尋視窗開不起來,
# 所以在這裡就吞掉,按鈕按下去才告訴使用者缺什麼。
try:
    import imgfind
    IMG_ERR = None
except Exception as _e:                            # noqa: BLE001
    imgfind = None
    IMG_ERR = str(_e)

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None

BG = "#f7f7f5"
ACCENT = "#1a5490"

# 中文介面字型。各平台內建的黑體不同名,開視窗後才能問系統有哪些,
# 所以先放一個安全預設,main() 裡再換成實際可用的那個。
FONT = "TkDefaultFont"

# 順序有意義:繁體字面的排前面。這台 Windows 的 Tk 回報的名稱是
# 「Microsoft JhengHei UI」而不是「Microsoft JhengHei」—— 少了 UI 兩字就會
# 一路掉到 Microsoft YaHei(簡體字面),部分字的字形會變成大陸寫法。
_FONT_PREFS = (
    "Microsoft JhengHei UI",  # Windows 微軟正黑體(Tk 實際回報的名字)
    "Microsoft JhengHei",
    "微軟正黑體",
    "PingFang TC",            # macOS 蘋方-繁
    "Heiti TC",               # macOS 較舊的版本
    "Apple LiGothic",
    "Noto Sans CJK TC",       # Linux
    "Microsoft YaHei",        # 最後才用簡體字面的
)


def pick_font(root):
    """挑一個這台機器真的有、而且有繁中字的黑體。"""
    from tkinter import font as tkfont
    have = set(tkfont.families(root))
    for f in _FONT_PREFS:
        if f in have:
            return f
    return tkfont.nametofont("TkDefaultFont").actual("family")


def mkbutton(parent, text, cmd, primary=False):
    """
    macOS 的 Aqua 主題會忽略 tk.Button 的 bg/fg,按鈕會變成一片灰白看不出來。
    所以 Mac 上改用 ttk.Button(外觀原生),Windows 維持原本的配色按鈕。
    """
    if findpile.IS_MAC:
        return ttk.Button(parent, text=text, command=cmd,
                          style="Accent.TButton" if primary else "TButton")
    if primary:
        return tk.Button(parent, text=text, command=cmd, bg=ACCENT, fg="white",
                         font=(FONT, 11, "bold"), relief="flat",
                         padx=18, pady=4, cursor="hand2")
    return tk.Button(parent, text=text, command=cmd, relief="flat", bg="#e4e4e0",
                     font=(FONT, 9), padx=10, cursor="hand2")


class App:
    def __init__(self, root):
        self.root = root
        self.rows = []
        self.con = None
        self.droots = [findpile.DEFAULT_ROOT]
        self.kws = []
        self.img_mode = False      # True = 現在顯示的是以圖搜圖的結果
        self.query_path = None     # 查詢用的那張照片
        # Tkinter 不會替你保留 PhotoImage 的參照,被 GC 掉的圖會變空白。
        # 一定要自己抓著。
        self._imgrefs = []
        root.title("檔案堆搜尋 — findpile")
        root.geometry("1180x740")
        root.minsize(900, 560)
        root.configure(bg=BG)

        self._build_top()
        self._build_split()
        self._build_status()

        root.bind("<Escape>", lambda e: self.kw.focus_set())
        root.bind("<Control-f>", lambda e: self.kw.focus_set())
        self.kw.focus_set()
        self.root.after(100, self.load_db)

    # ------------------------------------------------------------ 介面

    def _build_top(self):
        f = tk.Frame(self.root, bg=BG, padx=12, pady=10)
        f.pack(fill="x")

        tk.Label(f, text="關鍵字", bg=BG, font=(FONT, 11)).pack(side="left")
        self.kw = tk.Entry(f, font=(FONT, 14), width=34,
                           relief="solid", bd=1)
        self.kw.pack(side="left", padx=(8, 6), ipady=4)
        self.kw.bind("<Return>", lambda e: self.go())

        mkbutton(f, "搜尋", self.go, primary=True).pack(side="left")

        self.mode = tk.StringVar(value="any")
        box = tk.Frame(f, bg=BG)
        box.pack(side="left", padx=(16, 0))
        tk.Radiobutton(box, text="任一出現", variable=self.mode, value="any",
                       bg=BG, font=(FONT, 10)).pack(side="left")
        tk.Radiobutton(box, text="全部出現", variable=self.mode, value="all",
                       bg=BG, font=(FONT, 10)).pack(side="left")

        self.loose = tk.BooleanVar(value=False)
        tk.Checkbutton(f, text="模糊比對", variable=self.loose, bg=BG,
                       font=(FONT, 10)).pack(side="left", padx=(12, 0))

        mkbutton(f, "📷 以圖搜圖", self.go_image).pack(side="left", padx=(16, 0))

        tk.Label(f, text="　關鍵字用空白分開　·　掃描檔用短關鍵字(2字)",
                 bg=BG, fg="#888", font=(FONT, 9)).pack(side="left")

    def _build_split(self):
        pane = tk.PanedWindow(self.root, orient="vertical", bg=BG,
                              sashwidth=6, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # ---- 結果表
        top = tk.Frame(pane, bg=BG)
        cols = ("n", "score", "hits", "size", "src", "name", "dir")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for c, txt, w, anchor in (
                ("n", "#", 40, "center"), ("score", "分數", 60, "e"),
                ("hits", "命中", 60, "e"), ("size", "字數", 60, "e"),
                ("src", "來源", 60, "center"),
                ("name", "檔名", 400, "w"), ("dir", "資料夾", 380, "w")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anchor,
                             stretch=(c in ("name", "dir")))
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)
        self.tree.bind("<Double-1>", lambda e: self.open_file())
        self.tree.tag_configure("ocr", foreground="#8a5a00")
        pane.add(top, height=330)

        # ---- 前後文
        bot = tk.Frame(pane, bg=BG)
        bar = tk.Frame(bot, bg=BG)
        bar.pack(fill="x", pady=(6, 4))
        self.title = tk.Label(bar, text="點一下結果看前後文,雙擊開啟檔案", bg=BG,
                              fg="#555", font=(FONT, 10, "bold"),
                              anchor="w")
        self.title.pack(side="left", fill="x", expand=True)
        for txt, cmd in (("開啟檔案", self.open_file),
                         ("開啟所在資料夾", self.open_dir),
                         ("複製路徑", self.copy_path)):
            mkbutton(bar, txt, cmd).pack(side="left", padx=3)

        wrap = tk.Frame(bot)
        wrap.pack(fill="both", expand=True)
        self.txt = tk.Text(wrap, wrap="word", font=(FONT, 11),
                           relief="solid", bd=1, padx=10, pady=8,
                           spacing1=2, spacing3=8, bg="white")
        tsb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=tsb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.txt.tag_configure("hit", background="#ffe9a8", font=(
            FONT, 11, "bold"))
        self.txt.tag_configure("sep", foreground="#aaa")
        pane.add(bot)

    def _build_status(self):
        self.status = tk.Label(self.root, text="載入索引…", bg="#eceae5", fg="#444",
                               anchor="w", padx=12, pady=5,
                               font=(FONT, 9))
        self.status.pack(fill="x", side="bottom")

    # ------------------------------------------------------------ 資料

    def load_db(self):
        if not os.path.exists(findpile.DB_PATH):
            messagebox.showerror(
                "找不到索引",
                "還沒有建立索引。\n\n請先在這個資料夾開啟命令列,執行:\n\n"
                "    python findpile.py index")
            self.root.destroy()
            return
        try:
            self.con = findpile.db_open()
            n, ch = self.con.execute(
                "SELECT COUNT(*), COALESCE(SUM(nchars),0) FROM docs").fetchone()
            ocr = self.con.execute(
                "SELECT COUNT(*) FROM docs WHERE src LIKE 'ocr%'").fetchone()[0]
            # 索引可以同時收錄多個來源(從 Windows 複製來的那份 + 這台
            # 自己的資料夾),所以是一個清單。名稱不能叫 root ——
            # self.root 是 Tk 視窗本身。
            self.droots = findpile.display_roots(self.con)
            src = ("　·　來源 %d 處:%s"
                   % (len(self.droots),
                      "、".join(findpile.split_display(r.rstrip("\\/"))[1] or r
                                for r in self.droots))) if len(self.droots) > 1 else ""
            here = findpile.CONFIG.get("files_here", True)
            self.set_status("索引:%s 個檔案、%.0f 萬字(其中 %s 個是 OCR 辨識)%s%s　"
                            "輸入關鍵字後按 Enter"
                            % (format(n, ","), ch / 10000, format(ocr, ","), src,
                               "" if here else "　·　部分原始檔不在本機"))
        except Exception as e:
            messagebox.showerror("索引讀取失敗", str(e))
            self.root.destroy()

    def set_status(self, s):
        self.status.config(text=s)

    def go(self):
        kws = [k for k in self.kw.get().split() if k]
        if not kws:
            return
        self.set_status("搜尋中…")
        self.tree.delete(*self.tree.get_children())
        self.txt.delete("1.0", "end")
        threading.Thread(target=self._work,
                         args=(kws, self.mode.get(), self.loose.get()),
                         daemon=True).start()

    def _work(self, kws, mode, loose):
        con = None
        try:
            con = findpile.db_open()          # 每個執行緒要自己的連線
            rows, el = findpile.search(kws, mode, con, loose=loose)
            self.root.after(0, self._show, kws, rows, el)
        except Exception as e:
            self.root.after(0, self.set_status, "搜尋失敗:%s" % e)
        finally:
            if con is not None:
                con.close()                  # 不關的話每按一次 Enter 就洩漏一條

    def _show(self, kws, rows, el):
        self.kws = kws
        self.rows = rows[:300]
        # 從以圖搜圖切回關鍵字搜尋:欄位名稱要改回來,不然會留著「相似度」
        if self.img_mode:
            self.img_mode = False
            self._imgrefs = []
            for c, txt in (("score", "分數"), ("hits", "命中"),
                           ("size", "字數"), ("src", "來源")):
                self.tree.heading(c, text=txt)
        for i, r in enumerate(self.rows, 1):
            # 不能用 os.path.basename/dirname:索引裡可能是 Windows 路徑,
            # 在 Mac 上 posixpath 不認反斜線,整串會被當成一個檔名
            folder, name = findpile.split_display(
                findpile.strip_root_any(r["path"], self.droots))
            if len(self.droots) > 1:
                lab = findpile.root_label(r["path"], self.droots)
                folder = ("%s ▸ %s" % (lab, folder)) if folder else lab
            if r.get("nameonly"):
                # 影片、音檔、還沒 OCR 的圖片:抽不出內文,「命中 0 次 / 1K 字」
                # 這種顯示會讓人誤會,改成類型與檔案大小。
                hits = "📌 檔名" if r["namehit"] else "📁 資料夾"
                cells = (os.path.splitext(r["path"])[1].lstrip(".").upper(),
                         hits, findpile.fmt_size(r["size"]), "只有名稱")
            else:
                hits = str(r["total"]) if len(kws) == 1 else "%d／%s" % (
                    r["total"], "·".join(str(v) for v in r["counts"].values()))
                cells = ("%.1f" % r["score"],
                         hits + ("　📌" if r["namehit"] else ""),
                         "%.0fK" % r["kb"],
                         "OCR" if r["src"].startswith("ocr") else "")
            self.tree.insert("", "end", iid=str(i - 1), values=(
                (i,) + cells + (name, folder)),
                tags=("ocr",) if r["src"].startswith("ocr") else ())
        self.set_status("「%s」(%s%s)　命中 %s 個檔案,顯示前 %d 名　耗時 %.2f 秒"
                        % ("、".join(kws),
                           "全部出現" if self.mode.get() == "all" else "任一出現",
                           "+模糊" if self.loose.get() else "",
                           format(len(rows), ","), len(self.rows), el))
        if self.rows:
            self.tree.selection_set("0")
            self.tree.focus("0")

    # -------------------------------------------------------- 以圖搜圖

    def go_image(self):
        """挑一張照片(或直接貼上剪貼簿裡的圖),去比對索引裡的圖片。"""
        if imgfind is None:
            messagebox.showerror(
                "以圖搜圖無法使用",
                "缺少 opencv-python。\n\n請執行:\n\n    pip install opencv-python"
                "\n\n(原始錯誤:%s)" % IMG_ERR)
            return
        if Image is None:
            messagebox.showerror("以圖搜圖無法使用",
                                 "缺少 pillow。\n\n請執行:\n\n    pip install pillow")
            return
        if imgfind.stats(findpile.DATA_DIR) is None:
            messagebox.showinfo(
                "還沒建立圖片特徵",
                "以圖搜圖要先建立一次圖片特徵(約 15 分鐘,之後不必再跑)。\n\n"
                "請在這個資料夾開啟命令列,執行:\n\n    python findpile.py imgindex")
            return

        path = filedialog.askopenfilename(
            title="選一張照片(或先複製圖片再按取消,會改用剪貼簿)",
            filetypes=[("圖片", "*.jpg *.jpeg *.png *.heic *.heif *.bmp *.tif *.tiff *.webp"),
                       ("所有檔案", "*.*")])
        if not path:
            path = self._clipboard_image()
            if not path:
                return

        self.query_path = path
        self.set_status("以圖搜圖:%s …" % os.path.basename(path))
        self.tree.delete(*self.tree.get_children())
        self.txt.delete("1.0", "end")
        self._imgrefs = []
        threading.Thread(target=self._img_work, args=(path,), daemon=True).start()

    def _clipboard_image(self):
        """
        剪貼簿裡有圖就存成暫存檔回傳路徑。

        實際用法會是:用手機拍 -> 傳到電腦 -> 複製 -> 在這裡貼上,
        省掉先存檔的步驟。ImageGrab 只在 Windows 與 macOS 有效。
        """
        try:
            from PIL import ImageGrab
            im = ImageGrab.grabclipboard()
        except Exception:
            return None
        if not isinstance(im, Image.Image):
            return None          # 剪貼簿裡是文字或檔案清單,不是圖
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".png", prefix="findpile_q_")
        os.close(fd)
        im.convert("RGB").save(p)
        return p

    def _img_work(self, path):
        def prog(done, total, el, left):
            self.root.after(0, self.set_status,
                            "深度搜尋中 %d/%d　已 %.0f 秒,約剩 %.0f 秒"
                            % (done, total, el, left))
        try:
            rows, note = imgfind.search(path, findpile.DATA_DIR, progress=prog)
            self.root.after(0, self._show_img, rows, note)
        except Exception as e:                        # noqa: BLE001
            self.root.after(0, self._img_failed, e)

    def _img_failed(self, e):
        self.set_status("以圖搜圖失敗")
        messagebox.showerror("以圖搜圖失敗", str(e))

    def _show_img(self, rows, note):
        self.img_mode = True
        self.rows = rows
        # 欄位意義切換成圖片用的
        for c, txt in (("score", "相似度"), ("hits", "吻合度"),
                       ("size", "尺寸"), ("src", "方法")):
            self.tree.heading(c, text=txt)
        for i, r in enumerate(rows, 1):
            folder, name = findpile.split_display(
                findpile.strip_root_any(r["path"], self.droots))
            if len(self.droots) > 1:
                lab = findpile.root_label(r["path"], self.droots)
                folder = ("%s ▸ %s" % (lab, folder)) if folder else lab
            self.tree.insert("", "end", iid=str(i - 1), values=(
                i, "%.0f%%" % r["score"], r["detail"],
                "%dx%d" % (r["w"], r["h"]), r["how"],
                ("✓ " if r["sure"] else "? ") + name, folder),
                tags=() if r["sure"] else ("ocr",))
        self.set_status("%s　(✓=確定相符　?=可能相關,請自己看一眼)" % note)
        if rows:
            self.tree.selection_set("0")
            self.tree.focus("0")
        else:
            self.txt.insert("end", "找不到相似的圖片。\n\n", "sep")
            self.txt.insert("end",
                            "可能的原因:\n"
                            "　·　這張圖不在索引範圍裡\n"
                            "　·　拍得太歪或只拍到一小部分\n"
                            "　·　還沒建立圖片特徵(python findpile.py imgindex)\n")

    def _thumb(self, path, maxw=430):
        """讀成縮圖。回傳 ImageTk 物件,失敗回 None。"""
        try:
            if path.lower().endswith((".heic", ".heif")) and not imgfind.HAS_HEIC:
                return None
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((maxw, maxw * 2), Image.LANCZOS)
                return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _show_img_pair(self, r):
        """下方面板:上面放查詢圖,下面放找到的圖,方便直接用眼睛核對。"""
        self.txt.delete("1.0", "end")
        self._imgrefs = []

        bits = ["%s　相似度 %.0f%%" % (r["how"], r["score"])]
        if r["dist"] is not None:
            bits.append("pHash 差異 %d／64" % r["dist"])
        if r["inliers"] is not None:
            bits.append("幾何吻合點 %d" % r["inliers"])
        bits.append("✓ 確定相符" if r["sure"]
                    else "? 只是版面接近,沒通過幾何驗證")
        self.txt.insert("end", "　·　".join(bits) + "\n\n", "sep")

        for label, p in (("你的查詢圖", self.query_path), ("索引裡的圖", r["path"])):
            self.txt.insert("end", "%s\n" % label, "hit")
            th = self._thumb(p)
            if th is None:
                self.txt.insert("end", "(這張圖顯示不出來)\n\n")
            else:
                self._imgrefs.append(th)
                self.txt.image_create("end", image=th)
                self.txt.insert("end", "\n\n")
        self.txt.insert("end", "雙擊上方結果列可以直接開啟原始檔。\n", "sep")

    # ------------------------------------------------------------ 動作

    def cur(self):
        s = self.tree.selection()
        return self.rows[int(s[0])] if s else None

    def on_pick(self, _e=None):
        r = self.cur()
        if not r:
            return
        self.title.config(text=findpile.strip_root_any(r["path"], self.droots),
                          fg=ACCENT)
        if self.img_mode:
            self._show_img_pair(r)
            return
        self.txt.delete("1.0", "end")
        if r["src"].startswith("ocr"):
            self.txt.insert("end", "⚠ 這份是 OCR 辨識的掃描檔,可能有錯字。"
                                   "要引用內容請開啟原始檔核對。\n\n", "sep")
        if r.get("nameonly"):
            where = "檔名" if r["namehit"] else "資料夾名稱"
            self.txt.insert("end", "這個檔案抽不出內文(影片、音檔,或還沒 OCR "
                                   "的圖片),是靠%s找到的。\n\n大小 %s"
                            % (where, findpile.fmt_size(r["size"])))
            return
        segs = findpile.snippets(r["text"], self.kws, 12, 220)
        if not segs:
            self.txt.insert("end", "(只有檔名命中,內文沒有這個關鍵字)")
            return
        for i, s in enumerate(segs, 1):
            self.txt.insert("end", "%d. " % i, "sep")
            # 把關鍵字標黃
            pos = 0
            pat = re.compile("|".join(re.escape(k) for k in self.kws))
            for m in pat.finditer(s):
                self.txt.insert("end", s[pos:m.start()])
                self.txt.insert("end", m.group(), "hit")
                pos = m.end()
            self.txt.insert("end", s[pos:] + "\n")

    def _not_here(self, path):
        """
        只複製了索引、沒有原始檔的情形。丟一個錯誤訊息沒有用 ——
        使用者需要知道檔案在哪台機器的哪個位置,所以順手把完整路徑
        放進剪貼簿。
        """
        self.to_clipboard(path)
        messagebox.showinfo(
            "原始檔不在這台電腦",
            "這台只有索引,沒有檔案本身。\n\n完整路徑(已複製到剪貼簿):\n\n%s\n\n"
            "回到原本那台電腦貼上路徑就能開啟。\n"
            "若已經把資料複製到這台,請編輯 config.json 的 path_map。" % path)

    def to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def open_file(self):
        r = self.cur()
        if not r:
            return
        try:
            findpile.open_path(r["path"])
        except findpile.NotHere:
            self._not_here(r["path"])
        except Exception as e:
            messagebox.showerror("無法開啟", "%s\n\n%s" % (r["path"], e))

    def open_dir(self):
        r = self.cur()
        if not r:
            return
        try:
            findpile.reveal_path(r["path"])
        except findpile.NotHere:
            self._not_here(r["path"])

    def copy_path(self):
        r = self.cur()
        if not r:
            return
        self.to_clipboard(r["path"])
        self.set_status("已複製路徑:%s" % r["path"])


def nudge_redraw(root):
    """
    macOS 內建的 Tk 8.5 在 Mojave 之後有繪圖 bug:視窗開起來整片空白,
    要等使用者手動拉一下視窗大小才會把內容畫出來。
    開窗後把寬度 +1 再還原,等於幫使用者做那個動作。
    Tk 8.6 以上沒這問題,不必多此一舉。
    """
    if not findpile.IS_MAC or tk.TkVersion >= 8.6:
        return
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x, y = root.winfo_x(), root.winfo_y()
    root.geometry("%dx%d+%d+%d" % (w + 1, h, x, y))
    root.after(80, lambda: root.geometry("%dx%d+%d+%d" % (w, h, x, y)))


def main():
    global FONT
    if findpile.IS_WIN:
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)  # 高解析螢幕不要模糊
        except Exception:
            pass
    root = tk.Tk()
    FONT = pick_font(root)
    if findpile.IS_MAC:
        st = ttk.Style()
        st.configure("Accent.TButton", font=(FONT, 12, "bold"))
        st.configure("TButton", font=(FONT, 11))
    App(root)
    root.after(250, lambda: nudge_redraw(root))
    root.mainloop()


if __name__ == "__main__":
    main()
