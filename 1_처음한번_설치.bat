@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ===== 듣기 음원 만들기: 처음 한 번만 하는 설치 =====
echo.
python --version >nul 2>&1
if errorlevel 1 (
  echo [문제] 컴퓨터에 파이썬이 설치되어 있지 않습니다.
  echo.
  echo  1. 인터넷 주소창에 python.org/downloads 를 입력해 들어갑니다.
  echo  2. 노란색 "Download Python" 버튼으로 내려받아 실행합니다.
  echo  3. 설치 첫 화면 맨 아래 "Add python.exe to PATH" 에 반드시 체크하고 Install Now 를 누릅니다.
  echo  4. 설치가 끝나면 이 파일을 다시 더블클릭하세요.
  echo.
  pause
  exit /b 1
)
echo [1/3] 필요한 프로그램을 설치합니다. 인터넷이 필요하고 몇 분 걸릴 수 있습니다.
python -m pip install --upgrade pip
python -m pip install --upgrade edge-tts faster-whisper numpy av
echo.
echo [2/3] 설치 점검 중...
python -c "import edge_tts, numpy, av, faster_whisper; print('설치 점검 통과')"
if errorlevel 1 (
  echo.
  echo [문제] 설치에 문제가 있습니다. 인터넷 연결을 확인하고 이 파일을 한 번 더 실행해 보세요.
  echo        계속 안 되면 이 화면을 캡처해서 전달해 주세요.
  pause
  exit /b 1
)
echo.
echo [3/3] 끝났습니다. 이제 웹앱_실행.bat 을 더블클릭하세요.
echo (처음 음원을 만들 때 받아쓰기 모델 약 150MB를 한 번 내려받습니다.)
pause