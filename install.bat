@echo off
setlocal

echo 🐝 Installing SwarmMind...

:: Check for uv
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo 📦 Installing uv...
    curl -LsSf https://astral.sh/uv/install.ps1 | pwsh -c -
    set PATH=%USERPROFILE%\.cargo\bin;%PATH%
)

echo 🌱 Creating virtual environment...
call uv venv .venv
call .venv\Scripts\activate.bat

echo ⚙️ Installing dependencies...
call uv pip install -e ".[dev]"

echo ✅ Installation complete!
echo 🚀 Run 'swarmmind web' to start the UI.
pause
