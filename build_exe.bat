@echo off
echo ================================================
echo  Magyar Konyvkereso - EXE build
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo HIBA: Python nem talalhato!
    echo Toltsd le: https://www.python.org/downloads/
    echo Fontos: pipald be az "Add Python to PATH" opciot!
    pause
    exit /b 1
)

echo [1/4] Python OK. Csomagok telepitese...
python -m pip install --upgrade pip --quiet
python -m pip install requests beautifulsoup4 pyttsx3 pywin32 pyinstaller --quiet

if errorlevel 1 (
    echo HIBA: Csomagok telepitese sikertelen.
    pause
    exit /b 1
)

echo [2/4] Csomagok keszen. Build indul (1-3 perc)...
echo.

python -m PyInstaller --onefile --windowed --name "MagyarKonyvkereso" --hidden-import=pyttsx3.drivers --hidden-import=pyttsx3.drivers.sapi5 --hidden-import=win32com.client --hidden-import=win32con --hidden-import=win32api --hidden-import=tkinter --hidden-import=tkinter.scrolledtext --hidden-import=tkinter.filedialog --hidden-import=tkinter.messagebox --clean --noconfirm moly_kereses.py

if errorlevel 1 (
    echo HIBA: A build sikertelen!
    pause
    exit /b 1
)

echo.
echo [3/4] EXE masolasa...

if exist "dist\MagyarKonyvkereso.exe" (
    copy /Y "dist\MagyarKonyvkereso.exe" "MagyarKonyvkereso.exe" >nul
    rmdir /s /q build >nul 2>&1
    rmdir /s /q dist >nul 2>&1
    del /q MagyarKonyvkereso.spec >nul 2>&1
    echo [4/4] KESZ!
    echo.
    echo ================================================
    echo  Fajl: MagyarKonyvkereso.exe
    echo  Python nelkul fut barmelyik Windows gepen!
    echo ================================================
) else (
    echo HIBA: Az EXE nem jott letre.
    pause
    exit /b 1
)

echo.
pause
