#!/bin/bash
# =====================================================================
#  安裝Python.command —— macOS 專用,一次裝好 python.org 版 Python 3.13
#
#  為什麼要裝:
#    Apple 內建的 python3(3.9.6)搭的是 Tk 8.5.9,2011 年的版本。
#    它在 Mojave 之後的 macOS 上有繪圖 bug —— 搜尋視窗開起來整片空白,
#    要手動拉一下視窗大小內容才會出現。
#    python.org 的版本自帶 Tk 8.6,沒有這個問題。
#
#  不會動到系統的 /usr/bin/python3(macOS 自己要用,不能刪)。
#  新版裝在 /Library/Frameworks/Python.framework/,兩個並存。
# =====================================================================
set -u
cd "$(dirname "$0")" || exit 1

VER=3.13.17
PKG=python-$VER-macos11.pkg
URL=https://www.python.org/ftp/python/$VER/$PKG
SHA=3053592af66128269efab37fe7851a4894920b27c442aeec99ce4d34d17c07e3
PREFIX=/Library/Frameworks/Python.framework/Versions/3.13

die() { echo; echo "$1"; echo; printf '按 Enter 關閉…'; read -r _; exit 1; }

echo "================================================"
echo " 安裝 Python $VER (python.org 版)"
echo "================================================"
echo
echo "下載來源:$URL"
echo "大小約 68 MB。"
echo

# --- 1. 下載到暫存資料夾 --------------------------------------------
TMP=$(mktemp -d) || die "✗ 建不出暫存資料夾。"
trap 'rm -rf "$TMP"' EXIT

echo "--- 下載中 ---"
curl -fL --progress-bar -o "$TMP/$PKG" "$URL" \
  || die "✗ 下載失敗。檢查網路後再跑一次。"

# --- 2. 核對校驗碼(確認檔案沒被動過手腳才安裝) --------------------
GOT=$(shasum -a 256 "$TMP/$PKG" | awk '{print $1}')
if [ "$GOT" != "$SHA" ]; then
  die "✗ 檔案校驗碼對不上,為安全起見不安裝。

  期望:$SHA
  實得:$GOT"
fi
echo "✓ 校驗碼正確"
echo

# --- 3. 安裝(需要 Mac 開機密碼) ------------------------------------
echo "--- 安裝中 ---"
echo "接下來會問你 Mac 的開機密碼(裝到系統目錄需要)。"
echo "打密碼時畫面不會顯示任何字元,這是正常的,打完按 Enter。"
echo
sudo installer -pkg "$TMP/$PKG" -target / || die "✗ 安裝失敗。"
echo "✓ 已安裝到 $PREFIX"
echo

# --- 4. HTTPS 憑證 ---------------------------------------------------
CERT="/Applications/Python 3.13/Install Certificates.command"
if [ -f "$CERT" ]; then
  echo "--- 設定 HTTPS 憑證 ---"
  if "$CERT" >/dev/null 2>&1; then
    echo "✓ 憑證已設定"
  else
    echo "! 憑證設定沒成功(不影響搜尋功能)"
  fi
  echo
fi

# --- 5. 確認版本 -----------------------------------------------------
PY=$PREFIX/bin/python3
[ -x "$PY" ] || die "✗ 找不到剛裝好的 python3:$PY"
echo "--- 確認 ---"
echo "✓ python3  $("$PY" -V | cut -d' ' -f2)   $PY"
TKVER=$("$PY" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null)
case "$TKVER" in
  "")   die "✗ 新裝的 Python 沒有可用的 tkinter，視窗開不起來。

  請把這個畫面截圖下來。" ;;
esac
echo "✓ tkinter  Tk $TKVER"
case "$TKVER" in
  8.5*) echo "  ⚠ 還是 8.5,不對勁 —— 把這個畫面截圖下來。" ;;
  *)    echo "  (8.6 就對了,空白視窗的問題解決了)" ;;
esac
echo

# --- 6. 把 PDF 抽文套件裝進新的 Python -------------------------------
echo "--- 裝 PDF 抽文套件 ---"
"$PY" -m pip install --quiet --upgrade pip 2>/dev/null
if "$PY" -m pip install --quiet pymupdf pypdf 2>/dev/null; then
  echo "✓ pymupdf / pypdf"
else
  echo "! 套件沒裝成功。查詢不受影響,只是日後重建索引時 PDF 抽不到內文。"
fi

echo
echo "================================================"
echo " 完成。"
echo
echo " 請把「所有終端機視窗關掉」再重開一個,"
echo " 這樣 python3 才會指到新版。"
echo " 之後雙擊「開啟搜尋.command」即可。"
echo "================================================"
echo
printf '按 Enter 關閉…'; read -r _
