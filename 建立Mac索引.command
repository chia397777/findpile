#!/bin/bash
# =====================================================================
#  建立Mac索引.command —— 把這台 Mac 自己的檔案也加進搜尋範圍
#
#  預設掃四個地方:
#     ~/Documents                 文件
#     ~/Desktop                   桌面
#     ~/Downloads                 下載
#     ~/Library/Mobile Documents  iCloud 雲碟
#
#  不掃整個家目錄,因為 ~/Library 底下有幾十萬個應用程式檔案,
#  掃它只是浪費時間。要加其他資料夾見最後的說明。
#
#  重點:這個動作「只增加」,不會動到從 Windows 複製過來的那份索引。
#  程式判斷「檔案消失」時只在本次掃描的資料夾範圍內判斷。
# =====================================================================
set -u
cd "$(dirname "$0")" || exit 1

. "./_pick_python.sh"
if [ -z "${PY:-}" ]; then
  echo "找不到可用的 python3。請先雙擊「安裝.command」。"
  echo
  printf '按 Enter 關閉…'; read -r _
  exit 1
fi

echo "================================================"
echo " 把這台 Mac 的檔案加入索引"
echo "================================================"
echo
echo "要掃的資料夾:"
for d in "$HOME/Documents" "$HOME/Desktop" "$HOME/Downloads" \
         "$HOME/Library/Mobile Documents/com~apple~CloudDocs"; do
  if [ -d "$d" ]; then echo "  ✓ $d"; else echo "  - $d (沒有,略過)"; fi
done
echo
echo "第一次會跑一陣子(取決於檔案數量),之後更新只要幾秒。"
echo "隨時可以 Ctrl+C 中斷,下次會從中斷處接續。"
echo

# macOS 會在程式第一次讀取「文件」「桌面」「下載」時跳出授權對話框。
# 這裡先提醒,不然使用者會以為程式卡住了。
echo "※ macOS 可能會跳出「終端機想取取『文件』資料夾」的對話框,請按「好」。"
echo
read -r -p "按 Enter 開始…" _
echo

"$PY" findpile.py index --mac

echo
echo "================================================"
echo " 完成。開啟搜尋視窗就能一起查到了。"
echo "================================================"
echo
echo "想加其他資料夾(例如外接硬碟),在終端機執行:"
echo "  cd \"$(pwd)\""
echo "  python3 findpile.py index --dir /Volumes/你的硬碟/某資料夾"
echo
printf '按 Enter 關閉…'; read -r _
