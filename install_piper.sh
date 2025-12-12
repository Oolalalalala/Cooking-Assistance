#!/bin/bash

# --- Configuration ---
PIPER_VERSION="2023.11.14-2"
ARCH="x86_64" # Use 'aarch64' if running on Raspberry Pi 64-bit
INSTALL_DIR="$HOME/.local/bin"
MODEL_DIR="$HOME/piper/models"

# --- CORRECTED MODEL URLs ---
# Using the 'main' branch directly to avoid version tag issues
MODEL_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx"
CONFIG_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json"

# --- Setup ---
echo "🚀 Starting Piper Installation..."

# 1. Create Directories
echo "📂 Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$MODEL_DIR"

# 2. Download Piper Binary
echo "⬇️  Downloading Piper ($ARCH)..."
cd /tmp
# Using -c to continue partial downloads and -O to specify output
wget -c "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${ARCH}.tar.gz" -O piper.tar.gz

if [ $? -ne 0 ]; then
    echo "❌ Error: Failed to download Piper. Check your internet connection."
    exit 1
fi

# 3. Install Piper
echo "📦 Extracting and installing..."
tar -xf piper.tar.gz

# Copy binary and required libraries
# Ensure we copy from the extracted 'piper' folder
cp -r piper/* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/piper"

# 4. Download Voice Model
echo "⬇️  Downloading Chinese Voice Model..."
cd "$MODEL_DIR"
# Download Model
wget -c "$MODEL_URL" -O "zh_CN-huayan-medium.onnx"
# Download Config
wget -c "$CONFIG_URL" -O "zh_CN-huayan-medium.onnx.json"

# Verify download size (Model should be > 40MB)
FILESIZE=$(stat -c%s "zh_CN-huayan-medium.onnx" 2>/dev/null || stat -f%z "zh_CN-huayan-medium.onnx")
if [ "$FILESIZE" -lt 10000 ]; then
    echo "❌ Error: Model download failed (File is too small/broken)."
    exit 1
fi

# 5. Cleanup
rm -rf /tmp/piper /tmp/piper.tar.gz

# 6. Verification
echo "✅ Installation Complete!"
echo "---------------------------------------------------"
echo "Binary: $INSTALL_DIR/piper"
echo "Model:  $MODEL_DIR/zh_CN-huayan-medium.onnx"
echo "---------------------------------------------------"

# Check if it works
if "$INSTALL_DIR/piper" --version > /dev/null 2>&1; then
    echo "🎉 Piper is working correctly!"
    "$INSTALL_DIR/piper" --version
else
    echo "⚠️  Piper binary found but returned an error."
    echo "   Try running: sudo apt install libespeak-ng1"
fi