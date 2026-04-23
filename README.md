# RPI5 AI 展示系統 (Exhibition Demo)

在 **Raspberry Pi 5** 上運行的 Edge AI 語音互動與展示螢幕即時變色系統。  
具備「完全無狀態推論」與「守門員防呆機制」，專為高流量實體展場設計。**完全離線，不依賴雲端。**

---

## ✨ 核心功能特色

| 功能 | 說明 |
|------|------|
| 🎙️ 語音 + 打字輸入 | 訪客掃 QR Code 即可用語音或打字與 AI 對話 |
| 🎨 即時展示螢幕變色 | 說「把背景改成橘色」，大螢幕背景立即漸變 |
| 📢 多人廣播 | 顏色變更時，所有連線中的訪客手機會同時收到通知 |
| 🔒 離線 HTTPS | Let's Encrypt 憑證 + dnsmasq DNS，確保 Web Speech API 正常運作 |
| 📋 排隊機制 (Queue) | `asyncio.Lock` 序列推理，多人同時操作時模型不崩潰 |
| 🔄 開機自啟 | systemd 管理服務，斷電重開自動恢復 |

---

## 🛡️ 展場級穩定性優化 (Guardrails)

本系統具備針對小模型 (Small Language Models) 的多重防禦機制，確保 100% 執行準確率：

- **混合色主動拒絕**：偵測到「橘紅色」等衝突指令時，系統會優雅拒絕並引導重新輸入。
- **長度優先匹配 (Fast Path)**：精準解決字元重疊誤判問題（例如防止「紅」截胡「橘色」）。
- **KV Cache 強制重置**：每次對話前徹底清空底層模型快取（`LLM.reset()`），實現真正的無狀態推論，消除語意慣性。
- **守門員驗證 (Post-Validation)**：後驗檢查機制，只要正規表示式抓對意圖，直接強制覆蓋 LLM 的幻覺輸出。
- **佇列計數修正**：訪客在等待期間關掉瀏覽器時，自動修正 `queue_size`，避免計數器永久膨脹。

---

## 🏗️ 系統架構

```
訪客手機（HTTPS）
        ↓
Raspberry Pi 5
├── nginx（HTTPS 反向代理）
├── dnsmasq（本機 DNS + DHCP）
├── FastAPI（WebSocket 伺服器）
│   ├── GET  /             → 訪客語音 UI (index.html)
│   ├── GET  /display      → 展示螢幕頁面 (display.html)
│   ├── GET  /qr.png       → 動態產生 QR Code
│   ├── GET  /info         → 伺服器資訊（URL / Model / WiFi）
│   ├── GET  /stats        → 系統狀態（溫度 / 上線時間 / 連線數）
│   ├── WS   /ws           → 訪客對話與 AI 推論
│   └── WS   /ws/display   → 展示螢幕顏色與結果即時推播
└── llama-cpp-python（載入 GGUF，Gemma Function Calling 格式）
```

---

## 📁 目錄結構

```
rpi5-demo/
├── server.py               # 主伺服器（Gemma function calling + 守門員機制）
├── system_prompt.txt       # 嚴格定義的系統提示詞與工具格式
├── rebuild_llama.sh        # llama-cpp-python 重編譯腳本（修改 C++ 源碼後用）
├── models/                 # 放置 .gguf 模型檔（伺服器自動偵測）
│   └── README.md
├── templates/
│   ├── index.html          # 訪客語音 UI（掃碼後在手機開啟）
│   └── display.html        # 展示螢幕頁面（/display，接收即時顏色事件）
├── services/
│   └── rpi5-demo.service   # systemd 服務設定
├── setup_system.sh         # 一鍵安裝腳本（熱點 + Python 套件 + 開機自啟）
├── setup_https.sh          # HTTPS 設定腳本（nginx + dnsmasq）
├── RPI5_指令速查表.txt      # 所有常用指令一覽，可直接貼至終端機
└── 操作說明書_v2.docx      # 完整安裝與操作說明書
```

---

## 🚀 快速開始

### 1. 硬體需求

- Raspberry Pi 5（建議 4GB / 8GB RAM）
- MicroSD 32GB+
- 官方 27W USB-C 電源
- HDMI 螢幕（展示變色與訊息卡片用）

### 2. 放入模型

把你的 `.gguf` 檔案放入 `models/` 資料夾，伺服器啟動時會自動偵測並載入。  
針對本系統的 Prompt 格式，強烈建議使用 **Gemma 系列微調的 Function Calling 模型**，以獲得最佳解析效果。

> 也可透過環境變數 `MODEL_PATH` 指定絕對路徑。

### 3. 從本地編譯安裝 llama-cpp-python

目標：讓 `llama-cpp-python` 從源碼編譯，確保 ARM 硬體加速（NEON）與 KV Cache 控制功能正常。

```bash
# 安裝編譯工具
sudo apt install -y build-essential cmake git

# Clone llama-cpp-python（含內建 llama.cpp submodule）
cd ~
git clone --recursive https://github.com/abetlen/llama-cpp-python.git

# 從本地編譯安裝（約 5–15 分鐘）
cd ~/llama-cpp-python
CMAKE_ARGS="-DGGML_NEON=ON -DGGML_NATIVE=ON" \
pip install --no-binary :all: --force-reinstall . --break-system-packages
```

若之後修改了底層 `llama.cpp` C++ 源碼，使用 `rebuild_llama.sh` 快速重編：

```bash
bash rebuild_llama.sh
sudo systemctl start rpi5-demo
```

### 4. 安裝系統

```bash
# 從電腦傳送專案到 RPI5
scp -r rpi5-demo/ p400@rpi5-demo.local:/home/p400/

# 一鍵安裝（WiFi 熱點 + Python 套件 + 開機自啟）
cd /home/p400/rpi5-demo
sudo bash setup_system.sh
```

### 5. HTTPS 憑證設定

```bash
# 安裝 certbot
sudo apt update && sudo apt install -y certbot

# 申請憑證（以 DuckDNS 為例）
sudo certbot certonly \
  --manual \
  --preferred-challenges dns \
  --agree-tos \
  --email your@email.com \
  -d rpi5demo.duckdns.org
```

> 💡 certbot 暫停時，前往 DuckDNS 頁面：
> ```
> https://www.duckdns.org/update?domains=rpi5demo&token=你的token&txt=certbot給的驗證碼
> ```
> 看到 `OK` 後，等待 10–20 秒讓 DNS 傳播，再回 RPI5 按 Enter。

```bash
# 設定 nginx + dnsmasq
nano /home/p400/rpi5-demo/setup_https.sh
# 修改 DEMO_DOMAIN="rpi5demo.duckdns.org"
sudo bash setup_https.sh
```

### 6. 重開機

```bash
sudo reboot
```

---

## 📱 訪客使用流程

1. 手機連接 WiFi `RPI5-Demo`（密碼：`demo1234`）
2. 掃描大螢幕上的 QR Code，或直接開啟顯示的網址
3. 允許**麥克風**權限，點擊麥克風開始語音對話，或直接打字輸入

> ⚠️ 連線 WiFi 時，若手機出現「無網際網路連線」提示，請務必選擇 **「保持連線」**。

---

## 🎨 支援的展示指令（Function Call）

| 指令意圖 | 觸發效果 | 範例說法 |
|----------|----------|----------|
| 改變背景顏色 | 大螢幕背景漸變，手機端廣播通知 | 「把背景改成紅色」「換成橘色」 |
| 查詢城市天氣 | 回傳天氣資料並推播至大螢幕 | 「竹東天氣如何？」「高雄現在幾度」 |
| 改變 App 標題 | 模擬 UI 變更 | 「把標題改成你好」 |
| 顯示警告視窗 | 模擬 Alert 對話框 | 「顯示一個警告：歡迎光臨」 |

**支援的顏色清單：**  
`紅 (red)` · `綠 (green)` · `藍 (blue)` · `黃 (yellow)` · `紫 (purple)` · `橘 (orange)` · `黑 (black)` · `白 (white)`

> 系統具備**混色防呆機制**，輸入「橘紅色」等複合詞將會被安全攔截並提示重新輸入。

---

## ⚙️ 服務管理

```bash
# 重啟 AI 服務
sudo systemctl restart rpi5-demo

# 即時查看 Log（可觀察守門員攔截紀錄）
sudo journalctl -u rpi5-demo -f

# 過濾模型輸出
sudo journalctl -u rpi5-demo -f | grep "Model output"

# 開啟展示螢幕（Kiosk 模式，在 RPI5 桌面執行）
DISPLAY=:0 chromium-browser --kiosk --incognito --noerrdialogs http://localhost:8000/display &

# 手動 Debug 測試（先停止背景服務避免 Port 衝突）
sudo systemctl stop rpi5-demo
python3 server.py
```

---

## 🔧 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `PORT` | `8000` | 監聽埠 |
| `MODEL_PATH` | 自動偵測 | 指定 GGUF 絕對路徑 |
| `EXTERNAL_URL` | 自動偵測 IP | 系統對外的 URL（QR Code 使用） |
| `N_THREADS` | `4` | 綁定 CPU 執行緒數 |
| `N_CTX` | `2048` | 模型 Context 長度 |
| `MAX_TOKENS` | `512` | 單次推理最大 Token 數 |
| `TEMPERATURE` | `0.7` | 推理溫度，平衡穩定性與語意理解 |
| `HOTSPOT_SSID` | `RPI5-Demo` | 展示用 WiFi 名稱 |
| `HOTSPOT_PASS` | `demo1234` | 展示用 WiFi 密碼 |

---

## 🔌 API 端點一覽

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/` | 訪客語音 UI |
| `GET` | `/display` | 展示螢幕頁面（給外接螢幕開啟） |
| `GET` | `/qr.png` | 動態產生 QR Code 圖片 |
| `GET` | `/info` | 回傳 URL、模型名稱、WiFi 設定 |
| `GET` | `/stats` | 回傳 CPU 溫度、上線時間、連線人數 |
| `WS` | `/ws` | 訪客對話主通道（推理 + 廣播） |
| `WS` | `/ws/display` | 展示螢幕即時事件通道（顏色 / 結果卡片） |

---

## 🧠 Gemma Function Calling 格式

`server.py` 採用 **raw completion** 模式，自行管理 Gemma 格式 Prompt，不使用 `chat_format`：

```
<start_of_turn>developer
{system_prompt}
<end_of_turn>
<start_of_turn>user
{user_input}
<end_of_turn>
<start_of_turn>model
```

模型輸出的 Function Call 格式（支援多種解析路徑）：

```python
# Markdown 格式
```python
change_background_color(color="red")
```

# 官方標籤格式
<start_function_call>{"name": "change_background_color", "arguments": {"color": "red"}}<end_function_call>
```

---

## 📋 相關文件

- **`操作說明書_v2.docx`**：完整安裝與操作說明（9 章 + 附錄）
- **`RPI5_指令速查表.txt`**：所有指令一覽，可直接貼至終端機

---

## 🔴 展示前確認清單

```bash
ls -lh /home/p400/rpi5-demo/models/          # 確認 GGUF 模型存在
openssl x509 -enddate -noout \
  -in /etc/letsencrypt/live/rpi5demo.duckdns.org/fullchain.pem  # 憑證未過期
systemctl status rpi5-demo                    # 服務正常運行
nmcli connection show rpi5-hotspot            # WiFi 熱點正常
vcgencmd measure_temp                         # 溫度建議 < 70°C
free -h                                       # 記憶體使用狀況
curl http://localhost:8000/stats              # 連線人數與系統狀態
```

---

## 📄 License

MIT
