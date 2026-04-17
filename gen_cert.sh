#!/bin/bash
# ============================================================
# 憑證產生輔助腳本
# 在「有網路的環境」執行一次，然後把憑證複製到 RPI5
#
# 執行：bash gen_cert.sh
# ============================================================

DOMAIN="${1:-}"

if [ -z "$DOMAIN" ]; then
    echo ""
    echo "用法：bash gen_cert.sh your-domain.com"
    echo ""
    echo "還沒有網域？免費申請選項："
    echo "  - js.org       免費子網域（需 GitHub Pages）"
    echo "  - afraid.org   免費 DNS + 子網域"
    echo "  - duckdns.org  免費子網域 (xxx.duckdns.org)"
    echo ""
    exit 1
fi

echo ""
echo "網域：$DOMAIN"
echo ""
echo "選擇憑證產生方式："
echo "  1) 手動 DNS 驗證（任何 DNS 都行，需手動加 TXT 紀錄）"
echo "  2) Cloudflare 自動驗證（需 API Token）"
echo "  3) mkcert 本地憑證（僅限測試，需手動安裝根憑證）"
echo ""
read -p "選擇 [1/2/3]: " choice

case "$choice" in
1)
    echo ""
    echo "▶ 手動 DNS 驗證"
    echo "  步驟：certbot 會要求你在 DNS 加一筆 TXT 紀錄"
    echo "  加完後按 Enter 繼續，certbot 驗證通過就完成"
    echo ""
    sudo certbot certonly \
        --manual \
        --preferred-challenges dns \
        --agree-tos \
        -d "$DOMAIN"
    ;;
2)
    echo ""
    echo "▶ Cloudflare 自動驗證"
    echo "  請輸入 Cloudflare API Token（需有 DNS:Edit 權限）："
    read -s CF_TOKEN
    echo ""
    mkdir -p ~/.secrets
    cat > ~/.secrets/cloudflare.ini << EOF
dns_cloudflare_api_token = $CF_TOKEN
EOF
    chmod 600 ~/.secrets/cloudflare.ini
    pip install certbot-dns-cloudflare -q
    sudo certbot certonly \
        --dns-cloudflare \
        --dns-cloudflare-credentials ~/.secrets/cloudflare.ini \
        --agree-tos \
        -d "$DOMAIN"
    ;;
3)
    echo ""
    echo "▶ mkcert 本地憑證"
    if ! command -v mkcert &>/dev/null; then
        echo "  安裝 mkcert..."
        if command -v brew &>/dev/null; then
            brew install mkcert nss
        else
            sudo apt-get install -y libnss3-tools
            curl -sL "https://github.com/FiloSottile/mkcert/releases/latest/download/mkcert-linux-amd64" \
                -o /usr/local/bin/mkcert && chmod +x /usr/local/bin/mkcert
        fi
    fi
    mkcert -install
    mkdir -p /tmp/mkcert-certs
    cd /tmp/mkcert-certs
    mkcert "$DOMAIN" 192.168.4.1
    echo ""
    echo "  ⚠️  mkcert 憑證需要在每台展示手機上安裝根憑證！"
    echo "  根憑證位置：$(mkcert -CAROOT)/rootCA.pem"
    echo ""
    echo "  Android 安裝方式："
    echo "    設定 → 安全性 → 安裝憑證 → 選 rootCA.pem"
    echo "  iOS 安裝方式："
    echo "    1. 用 Safari 開啟 rootCA.pem 檔案"
    echo "    2. 設定 → 一般 → VPN 與裝置管理 → 安裝描述檔"
    echo "    3. 設定 → 一般 → 關於本機 → 憑證信任設定 → 啟用"
    echo ""
    ls -la /tmp/mkcert-certs/
    exit 0
    ;;
esac

# 複製憑證到 RPI5
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
echo ""
echo "憑證已產生：$CERT_DIR"
echo ""
echo "複製到 RPI5 的指令（在有憑證的機器上執行）："
echo ""
echo "  sudo rsync -avz /etc/letsencrypt/ pi@192.168.x.x:/tmp/letsencrypt/"
echo "  # 在 RPI5 上："
echo "  sudo mv /tmp/letsencrypt/ /etc/letsencrypt/"
echo "  sudo chown -R root:root /etc/letsencrypt/"
echo ""
echo "完成後執行：sudo bash setup_https.sh"
