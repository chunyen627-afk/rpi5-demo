#!/bin/bash
# ============================================================
# RPI5 展場螢幕啟動腳本（Kiosk 模式）
#
# 職責：只負責開啟展示螢幕頁面
# Server 由 systemd rpi5-demo.service 負責管理
#
# 用法：./start_demo.sh
# ============================================================

PORT=${PORT:-8000}
DISPLAY_URL="http://localhost:$PORT/display"

echo "======================================"
echo "  RPI5 展示螢幕啟動"
echo "======================================"
echo "  Server 狀態："
systemctl is-active rpi5-demo >/dev/null 2>&1 \
  && echo "  ✓ rpi5-demo.service 運行中" \
  || echo "  ⚠  rpi5-demo.service 未啟動，請執行：sudo systemctl start rpi5-demo"

echo ""
echo "  開啟 Kiosk 頁面：$DISPLAY_URL"

# 開啟 Chromium Kiosk 模式
if command -v chromium-browser &>/dev/null; then
    DISPLAY=:0 chromium-browser \
        --kiosk --incognito --noerrdialogs --disable-infobars --no-first-run \
        "$DISPLAY_URL" &
elif command -v chromium &>/dev/null; then
    DISPLAY=:0 chromium \
        --kiosk --incognito --noerrdialogs --disable-infobars \
        "$DISPLAY_URL" &
elif command -v firefox &>/dev/null; then
    DISPLAY=:0 firefox --kiosk "$DISPLAY_URL" &
else
    echo "  ⚠  找不到瀏覽器，請手動開啟：$DISPLAY_URL"
fi

# 等 server 就緒後印 QR Code（SSH 登入時方便查看）
sleep 3
python3 -c "
import qrcode, socket, os
port = int(os.getenv('PORT', 8000))
url = os.getenv('EXTERNAL_URL', '')
if not url:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]
    except: ip = '192.168.4.1'
    finally: s.close()
    url = f'http://{ip}:{port}'
print('')
print('  訪客 URL :', url)
print('')
qr = qrcode.QRCode(border=1)
qr.add_data(url)
qr.make(fit=True)
qr.print_ascii(invert=True)
" 2>/dev/null || true

echo "  提示：AI 伺服器由 systemd 在背景自動管理"
echo "======================================"
