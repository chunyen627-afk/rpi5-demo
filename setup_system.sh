#!/bin/bash
# ============================================================
# RPI5 AI 展示系統 — 完整安裝腳本
#
# 功能：
#   1. 安裝 Python 套件（llama-cpp-python、FastAPI 等）
#   2. 設定 WiFi 熱點（wlan0 → 192.168.4.1）
#   3. 設定開機自動啟動展示伺服器
#   4. 設定螢幕常亮（展示用）
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
SERVICE_USER="p400"   # 若你的使用者不是 pi，請修改這裡

echo ""
echo "╔══════════════════════════════════╗"
echo "║  RPI5 AI 展示系統 安裝程式       ║"
echo "╚══════════════════════════════════╝"
echo ""

# ── Step 1: 複製專案到 /opt ───────────────────────────────
echo "▶ [1/6] 安裝專案到 $DEMO_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$SCRIPT_DIR" != "$DEMO_DIR" ]; then
    mkdir -p "$DEMO_DIR"
    cp -r "$SCRIPT_DIR"/. "$DEMO_DIR/"
fi
mkdir -p "$DEMO_DIR/models"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DEMO_DIR"
echo "  ✓ 完成"

# ── Step 2: Python 套件 ────────────────────────────────────
echo ""
echo "▶ [2/6] 安裝 Python 套件"
echo "  安裝 llama-cpp-python（針對 ARM64 編譯，約 5-10 分鐘...）"

pip install \
    fastapi \
    uvicorn \
    qrcode[pil] \
    pillow \
    --break-system-packages -q

# llama-cpp-python：針對 RPI5 ARM64 優化
CMAKE_ARGS="-DGGML_NATIVE=ON -DGGML_NEON=ON" \
pip install llama-cpp-python --break-system-packages -q

echo "  ✓ 完成"

# ── Step 3: WiFi 熱點設定 ─────────────────────────────────
echo ""
echo "▶ [3/6] 設定 WiFi 熱點"

SSID="RPI5-Demo"
PASSWORD="demo1234"
HOTSPOT_IP="192.168.4.1"

# 檢查 NetworkManager
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

# 設定開機自動連線
nmcli connection modify "rpi5-hotspot" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100

echo "  ✓ 熱點建立完成"
echo "    SSID    : $SSID"
echo "    密碼    : $PASSWORD"
echo "    IP      : $HOTSPOT_IP"

# ── Step 4: systemd 服務 ───────────────────────────────────
echo ""
echo "▶ [4/6] 設定開機自動啟動"

# 更新 service 檔中的使用者
SERVICE_SRC="$DEMO_DIR/services/rpi5-demo.service"
sed -i "s/User=p400/User=$SERVICE_USER/" "$SERVICE_SRC"

cp "$SERVICE_SRC" /etc/systemd/system/rpi5-demo.service
systemctl daemon-reload
systemctl enable rpi5-demo.service
echo "  ✓ 服務已啟用（rpi5-demo.service）"

# ── Step 5: 螢幕常亮（展示用）────────────────────────────
echo ""
echo "▶ [5/6] 關閉螢幕省電（展示用）"

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

echo "  ✓ 螢幕省電已關閉"

# ── Step 6: 顯示 QR Code 的開機腳本 ─────────────────────
echo ""
echo "▶ [6/6] 設定開機後顯示 QR Code"

cat > /home/p400/rpi5-demo/show_qr.sh << 'EOF'
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
EOF
chmod +x /home/p400/rpi5-demo/show_qr.sh

# 加到 .bashrc（SSH 登入後自動顯示）
BASHRC="/home/$SERVICE_USER/.bashrc"
if ! grep -qF "show_qr.sh" "$BASHRC" 2>/dev/null; then
    echo "" >> "$BASHRC"
    echo "# RPI5 Demo — 顯示 QR Code" >> "$BASHRC"
    echo "[[ -t 0 && \$SHLVL -eq 1 ]] && /home/p400/rpi5-demo/show_qr.sh &" >> "$BASHRC"
fi

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
echo "╚══════════════════════════════════════════╝"
echo ""
