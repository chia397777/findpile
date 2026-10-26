@echo off
rem ====================================================================
rem  OCR全跑.bat  —— 把掃描檔／圖片辨識成文字,併入搜尋索引
rem
rem  一般的搜尋只找得到「本來就是文字」的檔案。掃描件和照片對它來說
rem  是一片像素,查不到。這支把它們辨識成文字補進同一個索引。
rem
rem  這是一次性的長時間工作(幾千張圖要跑好幾個小時),但:
rem    * 可以隨時按 Ctrl+C 中斷
rem    * 下次再執行會自動從中斷處接續,不會重做
rem    * 只用一部分核心,跑的時候電腦還能正常用
rem
rem  直接雙擊這個檔案即可開始。
rem ====================================================================
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ================================================
echo   辨識索引範圍裡的圖片檔
echo ================================================
python findpile.py ocr --scope . --engine both --workers 2 --threads 3
if errorlevel 1 goto :stopped

echo.
echo ================================================
echo   完成。現在用「查」就搜得到這些掃描檔了。
echo ================================================
echo.
echo   有些 PDF 本身是掃描的,抽不出文字。要連那些一起做:
echo       python findpile.py ocr --scope pdf
echo.
echo   只想做某個資料夾:
echo       python findpile.py ocr --scope 掃描/2024
echo.
goto :end

:stopped
echo.
echo   已中斷。再執行這個檔案就會從中斷處接續。
echo.

:end
pause
