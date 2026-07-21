#!/bin/bash
set -e

echo "🐝 Installing SwarmMind..."

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

echo "🌱 Creating virtual environment..."
uv venv .venv
source .venv/bin/activate

echo "⚙️ Installing dependencies..."
uv pip install -e ".[dev]"

# Create desktop shortcut for Linux
echo "🖥️ Creating desktop shortcut..."
cat << EOF > ~/.local/share/applications/swarmmind.desktop
[Desktop Entry]
Version=1.0
Name=SwarmMind
Comment=Multi-Agent AI Research
Exec=bash -c "cd $(pwd) && source .venv/bin/activate && swarmmind web"
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=Development;
EOF

echo "✅ Installation complete!"
echo "🚀 Run 'swarmmind web' to start the UI, or launch it from your applications menu."
