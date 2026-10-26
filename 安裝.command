#!/bin/bash
# =====================================================================
#  安裝.command —— macOS 第一次設定(在 Finder 裡雙擊執行)
#
#  會先看 config.json 判斷這台是哪種情況:
#
#    files_here = false   只有索引,沒有原始檔
#                         → 只檢查環境、裝套件,不建索引
#                         (在這裡建索引會把索引裡的 15,000 筆
#                          全部當成「已刪除」而清空)
#
#    沒有 config.json     原始檔也在這台
#                         → 檢查環境後建立索引
# =====================================================================
set -u
cd "$(dirname "$0")" || exit 1

die() { echo; echo "$1"; echo; printf '按 Enter 關閉…'; read -r _; exit 1; }

echo "================================================"
echo " findpile 安裝 (macOS)"
echo "================================================"
echo

# --- 1. python3 ------------------------------------------------------
. "./_pick_python.sh"
if [ -z "${PY:-}" ]; then
  die "✗ 找不到 python3。

  請到 https://www.python.org/downloads/macos/ 下載安裝。
  用 python.org 的安裝包,不要只用 Xcode 內建的那個 ——
  python.org 版自帶可用的 Tk 介面套件。"
fi
echo "✓ python3  $("$PY" -V | cut -d' ' -f2)   $PY"

# --- 2. tkinter(視窗介面) ------------------------------------------
if ! "$PY" -c "import tkinter" 2>/dev/null; then
  die "✗ 這個 python3 沒有 tkinter,視窗開不起來。

  用 Homebrew 安裝的話:  brew install python-tk
  或改裝 python.org 的版本(內建 tkinter)。"
fi
TKVER=$("$PY" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null)
echo "✓ tkinter  正常 (Tk $TKVER)"
case "$TKVER" in
  8.5*)
    echo
    echo "  ⚠ 這是 Apple 內建的 Tk 8.5（2011 年的版本）。"
    echo "    它在新版 macOS 上有繪圖 bug：視窗開起來是空白的。"
    echo "    程式裡已經做了繞過，但要根治請到 python.org"
    echo "    裝新版 Python（自帶 Tk 8.6）："
    echo "        https://www.python.org/downloads/macos/"
    ;;
esac

# --- 3. PDF 抽文套件 -------------------------------------------------
echo
echo "--- 安裝 PDF 抽文套件 ---"
"$PY" -m pip install --quiet --upgrade pip 2>/dev/null
if "$PY" -m pip install --quiet pymupdf pypdf 2>/dev/null; then
  echo "✓ pymupdf / pypdf"
else
  echo "! 套件沒裝成功。查詢功能不受影響(索引已經做好了);"
  echo "  只有在這台重新建索引時才需要它們。"
fi

# --- 3.5 新介面與以圖搜圖(都可以不裝) -------------------------------
echo
echo "--- 新介面 / 以圖搜圖套件(可選) ---"
if "$PY" -c "import PySide6" 2>/dev/null; then
  echo "✓ PySide6   已有,可雙擊「開啟搜尋_新介面.command」"
else
  printf '  PySide6 沒裝。要裝嗎?新介面比舊視窗好用很多。[Y/n] '
  read -r a
  case "${a:-Y}" in
    [Nn]*) echo "  跳過。舊介面「開啟搜尋.command」照樣能用。" ;;
    *)
      "$PY" -m pip install --user PySide6 2>&1 | tail -2
      if "$PY" -c "import PySide6" 2>/dev/null; then
        echo "✓ PySide6   裝好了"
      else
        # 裝得起來不等於 import 得動 —— PySide6 6.12 要 macOS 12 以上,
        # 更舊的系統上 pip 會成功,但載入 Qt 動態庫時才失敗。
        echo "! PySide6 裝了卻 import 不動(常見原因:macOS 版本太舊)。"
        echo "  不影響舊介面。"
      fi
      ;;
  esac
fi

# pillow-heif = iPhone 的 HEIC;opencv-python = 以圖搜圖。
# 這兩個只回報、不自動裝 —— 沒有它們也只是少功能,不會壞。
echo "  pillow_heif  $("$PY" -c "import pillow_heif" 2>/dev/null && echo 已有 || echo "沒裝(iPhone HEIC 會被跳過)")"
echo "  cv2          $("$PY" -c "import cv2" 2>/dev/null && echo 已有 || echo "沒裝(以圖搜圖不能用)")"
echo "  要補裝:$PY -m pip install --user pillow-heif opencv-python"

# --- 4. 索引 ---------------------------------------------------------
echo
INDEX_ONLY=no
if [ -f config.json ] && grep -q '"files_here"[[:space:]]*:[[:space:]]*false' config.json; then
  INDEX_ONLY=yes
fi

if [ "$INDEX_ONLY" = yes ]; then
  echo "--- 索引 ---"
  echo "config.json 標示這台沒有那份原始檔,所以不重建它的索引。"
  if [ -f index.db ]; then
    echo "現有的 index.db:"
  else
    echo "還沒有 index.db。從另一台複製過來後,用這行併進來:"
    echo "    python3 findpile.py merge ~/Downloads/index.db"
  fi
  "$PY" findpile.py stats 2>/dev/null | head -3
  echo
  echo "想把「這台 Mac 自己的檔案」也加進搜尋範圍:"
  echo "  雙擊「建立Mac索引.command」"
  echo "  (只會增加,不會動到上面這份索引)"
else
  echo "--- 建立索引 ---"
  echo "要掃的資料夾:$(cd .. && pwd)"
  echo "第一次會跑幾分鐘,之後更新只要幾秒。"
  echo
  "$PY" findpile.py index \
    || die "✗ 建立索引失敗。把上面的錯誤訊息截圖下來。"
fi

echo
echo "================================================"
echo " 完成。以後雙擊「開啟搜尋_新介面.command」即可"
echo " (沒有 PySide6 的話用「開啟搜尋.command」)。"
echo "================================================"
echo
printf '按 Enter 關閉…'; read -r _
