# =====================================================================
#  _pick_python.sh —— 挑一個「視窗畫得出來」的 python3
#
#  不能直接用 PATH 上的 python3。這台 Mac 的 python3 指到 Xcode 附的
#  3.9,搭 Tk 8.5.9 —— 在 Mojave 之後開窗是整片空白的。而且裝了新版
#  之後,先前開著的終端機 PATH 沒重讀、zsh 又把指令路徑快取住,還是會
#  抓到舊的。
#
#  所以這裡自己找:優先挑 tkinter 回報 Tk 8.6 以上的那個,新版在前。
#
#  兩個踩過的坑,都是「bash 和 zsh 行為不同」:
#    1. 候選清單不能用萬用字元。zsh 的 glob 沒對到檔案時會直接報錯,
#       bash 則是原樣保留。
#    2. 候選清單不能放在變數裡再展開。zsh 對未加引號的變數不做斷詞,
#       整串路徑會變成「一個字」,結果全部落空。所以直接寫在 for 裡。
#
#  用法:  . "$(dirname "$0")/_pick_python.sh"
#         舊介面(gui.py)用 $PY;新介面(app.py)用 pick_python_qt。
# =====================================================================

_tkver() { "$1" -c 'import tkinter; print(tkinter.TkVersion)' 2>/dev/null; }
_has_qt() { "$1" -c 'import PySide6' 2>/dev/null; }

# 候選清單只寫一份。印成一行一個,呼叫端用 while read 收 ——
# 這樣就完全避開上面那兩個坑:沒有萬用字元、也沒有未加引號的變數展開,
# bash 和 zsh 的行為一致。
_py_candidates() {
  cat <<'CANDIDATES'
/Library/Frameworks/Python.framework/Versions/3.16/bin/python3
/Library/Frameworks/Python.framework/Versions/3.15/bin/python3
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
/opt/homebrew/bin/python3
/usr/local/bin/python3
CANDIDATES
  command -v python3 2>/dev/null
}

pick_python() {
  _py_candidates | while IFS= read -r c; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    case "$(_tkver "$c")" in
      ""|8.4*|8.5*) continue ;;    # 沒 tkinter,或是會畫空白的舊 Tk
      *) printf '%s\n' "$c"; break ;;
    esac
  done
}

pick_python_qt() {
  # 新介面挑的是「import 得動 PySide6」,跟 Tk 版本無關。
  # 這兩件事在同一台機器上很可能落在不同的 python 上 —— 用 pick_python
  # 去挑新介面,會挑到沒有 PySide6 的那個,app.py 就靜靜退回舊視窗,
  # 看起來像新介面白做了。
  _py_candidates | while IFS= read -r c; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if _has_qt "$c"; then printf '%s\n' "$c"; break; fi
  done
}


# 一個 Tk 8.6 都沒有 —— 退而求其次,至少要開得起來
pick_python_any() {
  for c in "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if [ -n "$(_tkver "$c")" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

PY=$(pick_python)
[ -n "${PY:-}" ] || PY=$(pick_python_any)
