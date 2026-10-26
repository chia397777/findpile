#!/bin/bash
# =====================================================================
#  開啟搜尋.command —— macOS 啟動器(在 Finder 裡雙擊這個檔案)
#
#  .command 是 macOS 的「可雙擊 shell script」。它會開一個終端機視窗,
#  搜尋視窗出現後那個終端機就可以不管它(關掉終端機會一起關掉搜尋視窗,
#  所以把它縮到最小就好)。
#
#  第一次使用請先雙擊 安裝.command。
# =====================================================================
cd "$(dirname "$0")" || exit 1

. "./_pick_python.sh"
if [ -z "${PY:-}" ]; then
  echo "找不到可用的 python3。請先雙擊同一個資料夾裡的「安裝.command」。"
  echo
  printf '按 Enter 關閉…'; read -r _
  exit 1
fi

# exec 取代掉這個 shell,關終端機時才不會留下孤兒行程
exec "$PY" gui.py
