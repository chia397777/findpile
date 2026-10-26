@echo off
rem ====================================================================
rem  查.bat  —— findpile 的快捷啟動器
rem
rem  用法(在這個資料夾按右鍵「在終端中開啟」,或直接拖檔案進 cmd):
rem     查 報價
rem     查 報價 估價
rem     查 會議 紀錄 -all
rem     查 -index              重建/更新索引
rem     查 -stats              看索引狀態
rem
rem  報告會存到 _findpile\reports\ 並自動開啟。
rem ====================================================================
chcp 65001 >nul
setlocal

cd /d "%~dp0"

if "%~1"=="" goto :usage
if /i "%~1"=="-index"  ( python findpile.py index         & goto :end )
if /i "%~1"=="-full"   ( python findpile.py index --full   & goto :end )
if /i "%~1"=="-stats"  ( python findpile.py stats          & goto :end )

rem 收集關鍵字,並把 -all 轉成 --mode all
set KW=
set MODE=
:loop
if "%~1"=="" goto :run
if /i "%~1"=="-all" goto :setall
rem Quoted SET so a keyword containing & | ^ is taken literally;
rem no ( ) block, because cmd expands %1 before matching parentheses,
rem so a keyword containing ) would close the block early.
set "KW=%KW% %1"
shift
goto :loop
:setall
set "MODE=--mode all"
shift
goto :loop

:run
python findpile.py find %KW% %MODE% --open
goto :end

:usage
echo.
echo   用法:  查 ^<關鍵字^> [更多關鍵字...] [-all]
echo.
echo     查 報價
echo     查 報價 估價
echo     查 會議 紀錄 -all      ^(兩個詞都要出現^)
echo     查 -index                ^(更新索引^)
echo     查 -stats                ^(索引狀態^)
echo.

:end
endlocal
