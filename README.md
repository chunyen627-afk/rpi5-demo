# RPI5 AI 展示系統

> 在 Raspberry Pi 5 上運行 270M Gemma 模型，提供訪客語音互動與展示螢幕即時變色的 AI 展覽系統。完全離線，不依賴雲端。

---

## ✨ 功能特色

- 🎙️ **語音 + 打字輸入**：訪客掃 QR Code 即可用語音或打字與 AI 對話
- 🎨 **即時展示螢幕變色**：說「把背景改成紅色」，大螢幕背景立即漸變
- 📢 **多人廣播**：顏色變更時所有連線手機同時收到通知
- 🔒 **離線 HTTPS**：Let's Encrypt 憑證 + dnsmasq DNS，語音 API 正常運作
- 📋 **排隊機制**：asyncio Lock 序列推理，避免模型崩潰
- 🔄 **開機自啟**：systemd 管理服務，斷電重開自動恢復

---

## 🏗️ 系統架構

```
訪客手機（HTTPS）
      ↓
Raspberry Pi 5
  ├── nginx（HTTPS 反向代理）
  ├── dnsmasq（本機 DNS + DHCP）
  ├── FastAPI（WebSocket 伺服器）
  │   ├── /ws         → 訪客語音對話
  │   └── /ws/display → 展示螢幕顏色事件
  └── llama-cpp-python（270M GGUF 推理）
```

---

## 📁 目錄結構

```
rpi5-demo/
├── server.py              # 主伺服器（Gemma function calling + asyncio Lock）
├── system_prompt.txt      # Google Gemma 格式系統提示（含工具定義）
├── models/                # 放你的 .gguf 模型
│   └── README.md
├── templates/
│   ├── index.html         # 手機語音 UI
│   └── display.html       # 展示螢幕頁面（/display）
├── services/
│   └── rpi5-demo.service  # systemd 服務設定
├── setup_system.sh        # 一鍵安裝（熱點 + 套件 + 自啟）
├── setup_https.sh         # HTTPS 安裝（nginx + dnsmasq）
└── gen_cert.sh            # Let's Encrypt 憑證產生輔助（Mac/Linux 用）
```

---

## 🚀 快速開始

### 1. 硬體需求

- Raspberry Pi 5（建議 4GB RAM）
- MicroSD 32GB+
- 官方 27W USB-C 電源
- HDMI 螢幕（展示變色效果用）

### 2. 放入模型

把你的 `.gguf` 檔案放入 `models/` 資料夾，伺服器啟動時自動載入。

建議使用 Qwen2.5-0.5B-Instruct 的 Q4_K_M 量化版本（約 300MB），在 RPI5 上推理速度約 10–20 tokens/sec。

### 3. 安裝系統

```bash
# 從電腦傳送專案到 RPI5
scp -r rpi5-demo/ p400@rpi5-demo.local:/home/p400/

# SSH 進入 RPI5
ssh p400@rpi5-demo.local

# 一鍵安裝（WiFi 熱點 + Python 套件 + 開機自啟）
cd /home/p400/rpi5-demo
sudo bash setup_system.sh
```

### 4. HTTPS 憑證設定

**在 RPI5 上直接執行（Mac/Linux/Windows 都適用）**

```bash
# 在 RPI5 上安裝 certbot
sudo apt update && sudo apt install -y certbot

# 申請憑證（以 DuckDNS 為例）
sudo certbot certonly \
  --manual \
  --preferred-challenges dns \
  --agree-tos \
  --email your@email.com \
  -d rpi5demo.duckdns.org
```

certbot 暫停時，去 [duckdns.org](https://www.duckdns.org) 的 `current txt` 欄位貼上驗證碼，按 `update txt`，再回 RPI5 按 Enter。

```bash
# 設定 nginx + dnsmasq
nano /home/p400/rpi5-demo/setup_https.sh
# 修改 DEMO_DOMAIN="rpi5demo.duckdns.org"
sudo bash setup_https.sh
```

### 5. 重開機

```bash
sudo reboot
```

開機後：WiFi 熱點自動啟動、AI 伺服器自動啟動。

---

## 📱 訪客使用流程

1. 手機連接 WiFi `RPI5-Demo`（密碼：`demo1234`）
2. 掃描螢幕 QR Code，或開啟 `https://rpi5demo.duckdns.org`
3. 允許麥克風權限
4. 開始語音對話

> ⚠️ 連 WiFi 時若出現「無網際網路連線」提示，請選擇「保持連線」

---

## 🎨 支援的展示指令（Function Call）

| 指令說法 | 效果 |
|---|---|
| 「把背景改成紅色」 | /display 頁面背景漸變為紅色 |
| 「台北今天天氣如何？」 | 回傳模擬天氣資料 |
| 「把 App 標題改成 Hello」 | 模擬 UI 操作 |
| 「顯示一個提示：歡迎光臨」 | 模擬 Alert 對話框 |

支援顏色：`red` `green` `blue` `yellow` `purple` `orange`

---

## ⚙️ 服務管理

```bash
# 重啟服務
sudo systemctl restart rpi5-demo

# 查看即時 Log
sudo journalctl -u rpi5-demo -f

# 開啟展示螢幕（Kiosk 模式，在 RPI5 桌面執行）
chromium-browser --kiosk --incognito http://localhost:8000/display

# 手動測試（先停止背景服務避免 Port 衝突）
sudo systemctl stop rpi5-demo
python3 server.py
```

---

## 🔧 環境變數

| 變數 | 預設值 | 說明 |
|---|---|---|
| `PORT` | `8000` | 監聽埠 |
| `MODEL_PATH` | 自動偵測 | 指定 GGUF 路徑 |
| `EXTERNAL_URL` | 自動偵測 IP | 對外 URL |
| `N_THREADS` | `4` | CPU 執行緒數 |
| `N_CTX` | `2048` | Context 長度 |
| `CHAT_FORMAT` | `chatml` | 模型對話格式 |
| `HOTSPOT_SSID` | `RPI5-Demo` | WiFi 熱點名稱 |
| `HOTSPOT_PASS` | `demo1234` | WiFi 熱點密碼 |

---

## 📡 API 端點

| 端點 | 說明 |
|---|---|
| `GET /` | 手機語音 UI |
| `GET /display` | 展示螢幕頁面 |
| `GET /qr.png` | QR Code 圖片 |
| `GET /info` | 伺服器資訊 |
| `GET /stats` | 系統狀態（溫度、連線數） |
| `WS /ws` | 訪客對話 WebSocket |
| `WS /ws/display` | 展示螢幕顏色事件 WebSocket |

---

## 🔒 安全注意事項

- 請勿在 HIPAA、FedRAMP 等受管制環境使用
- Function Call 執行的系統指令已做白名單限制
- 展示結束後建議關閉熱點或更改密碼

---

## 📋 相關文件

- `操作說明書_v2.docx`：完整安裝與操作說明（9章 + 附錄）
- `RPI5_指令速查表.txt`：所有指令一覽，可直接貼到終端機

---

## 🛠️ 技術棧

- **推理引擎**：[llama-cpp-python](https://github.com/abetlen/llama-cpp-python)
- **Web 框架**：[FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/)
- **模型格式**：Google Gemma Function Calling（GGUF）
- **HTTPS**：Let's Encrypt + nginx + dnsmasq
- **前端**：Vanilla HTML/CSS/JS，Web Speech API

---

## 📄 License

MIT
