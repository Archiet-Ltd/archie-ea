@echo off
REM Entelim Platform - Quick Start Script for Windows
REM ========================================================

echo.
echo ===============================================
echo  Entelim Platform - Starting...
echo ===============================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found!
    echo Please run: python -m venv venv
    echo Then run: venv\Scripts\activate
    echo Then run: pip install -r requirements.txt
    pause
    exit /b 1
)

REM Activate virtual environment
echo [1/4] Activating virtual environment...
call venv\Scripts\activate.bat

REM Check if .env file exists
if not exist ".env" (
    echo ERROR: .env file not found!
    echo Please copy .env.example to .env and configure it.
    pause
    exit /b 1
)

echo [2/4] Loading environment variables from .env...
echo.

REM Check if PostgreSQL is running
echo [3/4] Checking database connection...
netstat -an | findstr "5439" >nul 2>&1
if errorlevel 1 (
    echo WARNING: PostgreSQL might not be running on port 5439
    echo Please start PostgreSQL before continuing.
    echo.
    set /p CONTINUE="Continue anyway? (y/n): "
    if /i not "%CONTINUE%"=="y" (
        pause
        exit /b 1
    )
)

echo [4/4] Starting Flask development server...
echo.
echo ===============================================
echo  Server will start at: http://localhost:5000
echo ===============================================
echo.
echo  Login Credentials:
echo  Email:    admin@example.com
echo  Password: admin123
echo.
echo  Press Ctrl+C to stop the server
echo ===============================================
echo.

REM Start Flask server
python manage.py runserver

pause
