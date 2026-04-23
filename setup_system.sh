#!/bin/bash
# ============================================================
# RPI5 AI 展示系統 — 完整安裝腳本
#
# 功能：
#   1. 複製專案到 /home/p400/rpi5-demo
#   2. 安裝 Python 套件（FastAPI、uvicorn、qrcode 等）
#      * llama-cpp-python 必須先單獨安裝（詳見說明書 3.0 節）
#   3. 檢查 llama-cpp-python 已從本地源碼編譯安裝
#   4. 設定 WiFi 熱點（wlan0 → 192.168.4.1）
#   5. 設定開機自動啟動展示伺服器（systemd）
#   6. 設定螢幕常亮 + 顯示 QR Code
#
# 執行：
#   sudo bash setup_system.sh
# ============================================================

set -e

# ── 必須 root ──────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "❌ 請用 sudo 執行：sudo bash setup_system.sh"
    exit 1
fi

DEMO_DIR="/home/p400/rpi5-demo"
SERVICE_USER="p400"

echo ""
echo "╔══════════════════════════════════╗"
echo "║  RPI5 AI 展示系統 安裝程式       ║"
echo "╚══════════════════════════════════╝"
echo ""

# ── Step 1: 複製專案 ──────────────────────────────────────
echo "▶ [1/6] 安裝專案到 $DEMO_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$SCRIPT_DIR" != "$DEMO_DIR" ]; then
    mkdir -p "$DEMO_DIR"
    cp -r "$SCRIPT_DIR"/. "$DEMO_DIR/"
fi
mkdir -p "$DEMO_DIR/models"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DEMO_DIR"
echo "  ✓ 完成"

# ── Step 2: Python 套件（不含 llama-cpp-python）────────────
echo ""
echo "▶ [2/6] 安裝 Python 套件"

pip install \
    fastapi \
    uvicorn \
    qrcode[pil] \
    pillow \
    --break-system-packages -q

echo "  ✓ 完成"

# ── Step 3: 檢查 llama-cpp-python 已自行編譯安裝 ─────────
echo ""
echo "▶ [3/6] 檢查 llama-cpp-python 安裝狀態"

if python3 -c "import llama_cpp" 2>/dev/null; then
    LLAMA_CPP_VER=$(python3 -c "import llama_cpp; print(llama_cpp.__version__)" 2>/dev/null || echo "unknown")
    echo "  ✓ llama-cpp-python 已安裝（版本 $LLAMA_CPP_VER）"
else
    echo "  ⚠️  llama-cpp-python 尚未安裝"
    echo ""
    echo "  請先完成說明書 3.0 節（從本地編譯安裝）："
    echo ""
    echo "    cd ~"
    echo "    git clone --recursive https://github.com/abetlen/llama-cpp-python.git"
    echo "    cd llama-cpp-python"
    echo '    CMAKE_ARGS="-DGGML_NATIVE=ON" \'
    echo "    pip install --no-binary :all: --force-reinstall . --break-system-packages"
    echo ""
    echo "  完成後再執行本腳本。"
    exit 1
fi

# ── Step 4: WiFi 熱點設定 ─────────────────────────────────
echo ""
echo "▶ [4/6] 設定 WiFi 熱點"

SSID="RPI5-Demo"
PASSWORD="demo1234"
HOTSPOT_IP="192.168.4.1"

if ! command -v nmcli &>/dev/null; then
    echo "  安裝 NetworkManager..."
    apt-get install -y network-manager -q
fi

# 移除舊的熱點設定（若存在）
nmcli connection delete "rpi5-hotspot" 2>/dev/null || true

# 建立熱點
nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name "rpi5-hotspot" \
    autoconnect yes \
    ssid "$SSID" \
    mode ap \
    ipv4.method shared \
    ipv4.addresses "$HOTSPOT_IP/24" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASSWORD" \
    802-11-wireless.band bg \
    802-11-wireless.channel 6

nmcli connection modify "rpi5-hotspot" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100

echo "  ✓ 熱點建立完成"
echo "    SSID    : $SSID"
echo "    密碼    : $PASSWORD"
echo "    IP      : $HOTSPOT_IP"

# ── Step 5: systemd 服務 ───────────────────────────────────
echo ""
echo "▶ [5/6] 設定開機自動啟動"

SERVICE_SRC="$DEMO_DIR/services/rpi5-demo.service"
sed -i "s/User=p400/User=$SERVICE_USER/" "$SERVICE_SRC"

cp "$SERVICE_SRC" /etc/systemd/system/rpi5-demo.service
systemctl daemon-reload
systemctl enable rpi5-demo.service
echo "  ✓ 服務已啟用（rpi5-demo.service）"

# ── Step 6: 螢幕常亮 + QR Code ────────────────────────────
echo ""
echo "▶ [6/6] 設定螢幕常亮 + 開機顯示 QR Code"

# DPMS（X11 螢幕省電）
if [ -f /etc/xdg/lxsession/LXDE-pi/autostart ]; then
    AUTOSTART="/etc/xdg/lxsession/LXDE-pi/autostart"
    grep -qF "@xset s off" "$AUTOSTART" || echo "@xset s off" >> "$AUTOSTART"
    grep -qF "@xset -dpms" "$AUTOSTART" || echo "@xset -dpms" >> "$AUTOSTART"
    grep -qF "@xset s noblank" "$AUTOSTART" || echo "@xset s noblank" >> "$AUTOSTART"
fi

# 防止 console 螢幕關閉
if ! grep -q "consoleblank=0" /boot/firmware/cmdline.txt 2>/dev/null; then
    sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
fi

# 顯示 QR Code 腳本
cat > /home/p400/rpi5-demo/show_qr.sh << 'QREOF'
#!/bin/bash
# 等待伺服器啟動後在終端顯示 QR Code
sleep 8
echo ""
python3 -c "
import qrcode, socket, os
port = int(os.getenv('PORT', 8000))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try: s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]
except: ip='192.168.4.1'
finally: s.close()
url = f'http://{ip}:{port}'
print('═'*44)
print(f'  展示 URL: {url}')
print('═'*44)
qr = qrcode.QRCode(border=1)
qr.add_data(url)
qr.make(fit=True)
qr.print_ascii(invert=True)
print(f'  WiFi: RPI5-Demo  密碼: demo1234')
print('═'*44)
"
QREOF
chmod +x /home/p400/rpi5-demo/show_qr.sh

# 加到 .bashrc（SSH 登入後自動顯示）
BASHRC="/home/$SERVICE_USER/.bashrc"
if ! grep -qF "show_qr.sh" "$BASHRC" 2>/dev/null; then
    echo "" >> "$BASHRC"
    echo "# RPI5 Demo — 顯示 QR Code" >> "$BASHRC"
    echo "[[ -t 0 && \$SHLVL -eq 1 ]] && /home/p400/rpi5-demo/show_qr.sh &" >> "$BASHRC"
fi

echo "  ✓ 完成"

# ── 完成 ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  安裝完成！                               ║"
echo "╠══════════════════════════════════════════╣"
echo "║  下一步：                                 ║"
echo "║  1. 把 .gguf 放入 /home/p400/rpi5-demo/models/ ║"
echo "║  2. 重新開機：sudo reboot                 ║"
echo "║  3. 開機後熱點自動啟動                    ║"
echo "║     WiFi: RPI5-Demo  密碼: demo1234       ║"
echo "║  4. 伺服器自動在 http://192.168.4.1:8000  ║"
echo "╠══════════════════════════════════════════╣"
echo "║  手動控制服務：                           ║"
echo "║  sudo systemctl start  rpi5-demo         ║"
echo "║  sudo systemctl stop   rpi5-demo         ║"
echo "║  sudo journalctl -u rpi5-demo -f         ║"
echo "╠══════════════════════════════════════════╣"
echo "║  修改 llama.cpp 源碼後重編：              ║"
echo "║  cd ~/rpi5-demo && ./rebuild_llama.sh    ║"
echo "║  sudo systemctl restart rpi5-demo        ║"
echo "╚══════════════════════════════════════════╝"
echo ""
