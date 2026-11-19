@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
  echo [문제] 파이썬이 없습니다. 먼저 1_처음한번_설치.bat 을 실행해 안내를 따라 주세요.
  pause
  exit /b 1
)
python -c "import edge_tts, numpy, av, faster_whisper" >nul 2>&1
if errorlevel 1 (
  echo [문제] 필요한 프로그램이 아직 설치되지 않았습니다. 먼저 1_처음한번_설치.bat 을 실행해 주세요.
  pause
  exit /b 1
)
:MENU
cls
echo.
echo ==================================================
echo   듣기 음원 만들기: 통합 실행기
echo ==================================================
echo  1. 듣기 음원 만들기 웹앱 시작 (기본 도구)
echo  2. 모바일 웹 응시 페이지 만들기 (독립 OMR 시험지)
echo  3. 종료
echo ==================================================
set /p CHOICE="원하는 작업 번호를 입력하세요 (1-3) [기본값: 1]: "

if "%CHOICE%"=="" set CHOICE=1
if "%CHOICE%"=="1" goto RUN_WEBAPP
if "%CHOICE%"=="2" goto RUN_EXAM_MAKER
if "%CHOICE%"=="3" goto END

echo.
echo 잘못된 입력입니다. 1에서 3 사이의 번호를 입력하세요.
timeout /t 2 >nul
goto MENU

:RUN_WEBAPP
echo.
echo 듣기 음원 만들기 웹앱을 시작합니다...
echo (잠시 후 인터넷 창이 자동으로 열립니다. 이 검은 창은 닫지 말고 두세요.)
echo.
python app.py
echo.
echo 웹앱이 종료되었습니다. 오류가 있었다면 위 내용을 캡처해서 알려 주세요.
pause
goto END

:RUN_EXAM_MAKER
echo.
python make_mobile_exam.py
pause
goto MENU

:END