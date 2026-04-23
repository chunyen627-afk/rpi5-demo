#!/bin/bash
# ============================================================
# RPI5 離線 HTTPS 展示系統 — 完整安裝腳本
#
# 原理：
#   1. RPI5 開 WiFi 熱點
#   2. dnsmasq 把你的域名解析到 192.168.4.1（本機）
#   3. nginx 用真實的 Let's Encrypt 憑證跑 HTTPS
#   4. 手機連熱點後，瀏覽器看到有效 HTTPS → 麥克風 API 開放
#
# 前置作業（只需一次，需要有網路的環境下做）：
#   1. 準備一個你擁有的網域（推薦免費的 DuckDNS）
#   2. 在 RPI5 上執行 certbot 產生憑證（詳見說明書 4.4 節）
#   3. 憑證有效期 90 天，到期前需重新執行
#
# 前提：setup_system.sh 已執行過（含 Python 套件與 llama-cpp-python）
#
# 執行：
#   sudo bash setup_https.sh
# ============================================================

set -e
[ "$EUID" -ne 0 ] && { echo "請用 sudo 執行"; exit 1; }

DEMO_DIR="/home/p400/rpi5-demo"
SERVICE_USER="${SUDO_USER:-p400}"

# ── 設定你的網域 ──────────────────────────────────────────
# 修改這兩行！
DEMO_DOMAIN="demo.example.com"   # 改成你的網域
HOTSPOT_IP="192.168.4.1"
HOTSPOT_SSID="RPI5-Demo"
HOTSPOT_PASS="demo1234"
PORT=8000

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  RPI5 離線 HTTPS 展示系統 安裝            ║"
echo "╚══════════════════════════════════════════╝"
echo "  網域: $DEMO_DOMAIN"
echo "  熱點: $HOTSPOT_SSID / $HOTSPOT_PASS"
echo ""

# ── 1. 安裝套件 ────────────────────────────────────────────
echo "▶ [1/6] 安裝系統套件"
apt-get update -q
apt-get install -y nginx dnsmasq certbot python3-certbot-dns-cloudflare -q 2>/dev/null \
  || apt-get install -y nginx dnsmasq certbot -q
# 注意：不在此安裝 llama-cpp-python，避免覆蓋使用者自行編譯的版本
# Python 套件應由 setup_system.sh 先安裝完成
echo "  ✓ 套件安裝完成（nginx + dnsmasq + certbot）"

# ── 2. 確認 WiFi 熱點已存在（由 setup_system.sh 建立）──────
echo ""
echo "▶ [2/6] 確認 WiFi 熱點"
if nmcli connection show "rpi5-hotspot" &>/dev/null; then
    echo "  ✓ 熱點已存在（rpi5-hotspot）"
else
    echo "  ⚠️  找不到熱點設定，請先執行 setup_system.sh"
    exit 1
fi

# ── 3. dnsmasq — 本機 DNS 欺騙 ────────────────────────────
echo ""
echo "▶ [3/6] 設定 dnsmasq（本機 DNS）"

# 備份原設定
[ -f /etc/dnsmasq.conf ] && cp /etc/dnsmasq.conf /etc/dnsmasq.conf.bak

cat > /etc/dnsmasq.conf << EOF
# RPI5 Demo — 本機 DNS
# 把展示網域指向 RPI5 自己
interface=wlan0
bind-interfaces
dhcp-range=192.168.4.2,192.168.4.254,24h
dhcp-option=6,$HOTSPOT_IP          # DNS server = 自己

# 關鍵：把展示網域解析到 RPI5 本機
address=/$DEMO_DOMAIN/$HOTSPOT_IP

# 其他 DNS 查詢交給上游（這台沒網路所以會 timeout，正常）
no-resolv
EOF

systemctl enable dnsmasq
systemctl restart dnsmasq || true
echo "  ✓ $DEMO_DOMAIN → $HOTSPOT_IP"

# ── 4. 憑證檢查 ────────────────────────────────────────────
echo ""
echo "▶ [4/6] 檢查 HTTPS 憑證"

CERT_PATH="/etc/letsencrypt/live/$DEMO_DOMAIN/fullchain.pem"
KEY_PATH="/etc/letsencrypt/live/$DEMO_DOMAIN/privkey.pem"

if [ ! -f "$CERT_PATH" ]; then
    echo ""
    echo "  ⚠️  找不到憑證！"
    echo ""
    echo "  請先在「有網路的環境」執行以下指令產生憑證："
    echo ""
    echo "  ─────────────────────────────────────────────"
    echo "  # 方法：手動 DNS 驗證（適用 DuckDNS 等任何 DNS 服務）"
    echo "  certbot certonly --dns-cloudflare \\"
    echo "    --dns-cloudflare-credentials ~/.secrets/cloudflare.ini \\"
    echo "    -d $DEMO_DOMAIN"
    echo ""
    echo "  # 方法 2：手動 DNS 驗證（任何 DNS 都行）"
    echo "  certbot certonly --manual --preferred-challenges dns \\"
    echo "    -d $DEMO_DOMAIN"
    echo "  ─────────────────────────────────────────────"
    echo ""
    echo "  產生後把整個 /etc/letsencrypt/ 資料夾複製到 RPI5，"
    echo "  然後重新執行這個安裝腳本。"
    echo ""
    echo "  RPI5 直接申請的方式："
    echo ""

    # 臨時用自簽憑證撐著（手機會有警告，但功能可以先測）
    echo "  先建立自簽憑證供測試（手機會有安全警告）..."
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
      -keyout "/etc/ssl/private/rpi5-demo.key" \
      -out    "/etc/ssl/certs/rpi5-demo.crt" \
      -subj "/CN=$DEMO_DOMAIN" \
      -addext "subjectAltName=IP:$HOTSPOT_IP,DNS:$DEMO_DOMAIN" \
      2>/dev/null
    CERT_PATH="/etc/ssl/certs/rpi5-demo.crt"
    KEY_PATH="/etc/ssl/private/rpi5-demo.key"
    echo "  ⚠️  自簽憑證已建立，僅供測試，正式展示請換成 Let's Encrypt"
else
    echo "  ✓ 憑證存在：$CERT_PATH"
    # 顯示到期日
    EXPIRY=$(openssl x509 -enddate -noout -in "$CERT_PATH" 2>/dev/null | cut -d= -f2)
    echo "  到期日：$EXPIRY"
fi

# ── 5. nginx 設定 ──────────────────────────────────────────
echo ""
echo "▶ [5/6] 設定 nginx HTTPS"

cat > /etc/nginx/sites-available/rpi5-demo << EOF
# HTTP → HTTPS 跳轉
server {
    listen 80;
    server_name $DEMO_DOMAIN $HOTSPOT_IP;
    return 301 https://$DEMO_DOMAIN\$request_uri;
}

# HTTPS → FastAPI
server {
    listen 443 ssl http2;
    server_name $DEMO_DOMAIN;

    ssl_certificate     $CERT_PATH;
    ssl_certificate_key $KEY_PATH;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # WebSocket
    location /ws {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host \$host;
        proxy_read_timeout 300s;
    }

    # 其他請求
    location / {
        proxy_pass       http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

ln -sf /etc/nginx/sites-available/rpi5-demo /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx
echo "  ✓ HTTPS on $DEMO_DOMAIN"

# ── 6. systemd 服務 ────────────────────────────────────────
echo ""
echo "▶ [6/6] 設定開機自動啟動"

sed "s/User=p400/User=$SERVICE_USER/g" \
  "$DEMO_DIR/services/rpi5-demo.service" \
  > /etc/systemd/system/rpi5-demo.service

# 加上 HTTPS URL
sed -i "s|# Environment=EXTERNAL_URL=.*|Environment=EXTERNAL_URL=https://$DEMO_DOMAIN|" \
  /etc/systemd/system/rpi5-demo.service

systemctl daemon-reload
systemctl enable rpi5-demo

# ── 完成 ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  安裝完成！                                   ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  訪客流程：                                   ║"
echo "║  1. 手機連 WiFi「$HOTSPOT_SSID」              "
echo "║     密碼：$HOTSPOT_PASS                       "
echo "║  2. 掃 QR Code 或瀏覽 https://$DEMO_DOMAIN    "
echo "║  3. 允許麥克風 → 語音輸入                     ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  把 .gguf 放入：$DEMO_DIR/models/             ║"
echo "║  重開機後全部自動啟動                         ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
