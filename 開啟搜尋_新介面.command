#!/bin/bash
# =====================================================================
#  開啟搜尋_新介面.command —— macOS 啟動器(新介面,PySide6)
#
#  和「開啟搜尋.command」只差一件事:挑 python 的標準不同。
#  舊介面要的是「Tk 8.6 以上」,新介面要的是「import 得動 PySide6」——
#  同一台機器上這兩個條件很可能落在不同的 python 上,所以不能共用。
#
#  找不到有 PySide6 的 python 就自動開舊介面,不會變成「雙擊沒反應」。
#  第一次使用請先雙擊「安裝.command」。
# =====================================================================
set -u
cd "$(dirname "$0")" || exit 1

. "./_pick_python.sh"

PYQT=$(pick_python_qt)
if [ -n "${PYQT:-}" ]; then
  # exec 取代掉這個 shell,關終端機時才不會留下孤兒行程
  exec "$PYQT" app.py
fi

echo "這台的 python 都沒有 PySide6,改開舊介面(gui.py)。"
echo
if [ -z "${PY:-}" ]; then
  echo "而且也找不到能開窗的 python3 —— 請先雙擊「安裝.command」。"
  echo
  printf '按 Enter 關閉…'; read -r _
  exit 1
fi
echo "要用新介面,執行這行之後再重開一次:"
echo "    $PY -m pip install --user PySide6"
echo
exec "$PY" gui.py
