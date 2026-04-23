RPI5 AI 展示系統 (Exhibition Demo)在 Raspberry Pi 5 上運行的 Edge AI 語音互動與展示螢幕即時變色系統。具備「完全無狀態推論」與「守門員防呆機制」，專為高流量實體展場設計。完全離線，不依賴雲端。✨ 核心功能特色🎙️ 語音 + 打字輸入：訪客掃 QR Code 即可用語音或打字與 AI 對話🎨 即時展示螢幕變色：說「把背景改成橘色」，大螢幕背景立即漸變📢 多人廣播：顏色變更時，所有連線中的訪客手機會同時收到通知🔒 離線 HTTPS：Let's Encrypt 憑證 + dnsmasq DNS，確保 Web Speech API 正常運作📋 排隊機制 (Queue)：asyncio Lock 序列推理，多人同時操作時模型不崩潰🔄 開機自啟：systemd 管理服務，斷電重開自動恢復🛡️ 展場級穩定性優化 (Guardrails)本系統具備針對小模型 (Small Language Models) 的多重防禦機制，確保 100% 執行準確率：混合色主動拒絕：偵測到「橘紅色」等衝突指令時，系統會優雅拒絕並引導重新輸入。長度優先匹配 (Fast Path)：精準解決字元重疊誤判問題（例如防止 "紅" 截胡 "橘色"）。KV Cache 強制重置：每次對話前徹底清空底層模型快取 (LLM.reset())，實現真正的無狀態推論，消除語意慣性。守門員驗證 (Post-Validation)：後驗檢查機制，只要正規表示式抓對意圖，直接強制覆蓋 LLM 的幻覺輸出。🏗️ 系統架構訪客手機（HTTPS）
      ↓
Raspberry Pi 5
  ├── nginx（HTTPS 反向代理）
  ├── dnsmasq（本機 DNS + DHCP）
  ├── FastAPI（WebSocket 伺服器）
  │   ├── /ws         → 訪客對話與推論
  │   └── /ws/display → 展示螢幕顏色與結果推播
  └── llama-cpp-python（載入 GGUF，自帶 KV Cache 清理機制）
📁 目錄結構rpi5-demo/
├── server.py              # 主伺服器（Gemma function calling + 守門員機制）
├── system_prompt.txt      # 嚴格定義的系統提示詞與工具格式
├── models/                # 放置 .gguf 模型檔
│   └── README.md
├── templates/
│   ├── index.html         # 手機語音 UI
│   └── display.html       # 展示螢幕頁面（/display）
├── services/
│   └── rpi5-demo.service  # systemd 服務設定
├── setup_system.sh        # 一鍵安裝腳本（熱點 + 套件 + 自啟）
├── setup_https.sh         # HTTPS 安裝腳本（nginx + dnsmasq）
├── rebuild_llama.sh       # llama-cpp-python 重編譯腳本
├── RPI5_指令速查表.txt       # RPI5 常用指令清單
└── 操作說明書_v2.md          # 完整安裝與操作說明書 (Markdown 版)
🚀 快速開始1. 硬體需求Raspberry Pi 5（建議 4GB/8GB RAM）MicroSD 32GB+官方 27W USB-C 電源HDMI 螢幕（展示變色與天氣資訊卡片用）2. 放入模型把你的 .gguf 檔案放入 models/ 資料夾，伺服器啟動時會自動偵測並載入。針對本系統的 Prompt 格式，強烈建議使用 Gemma 系列微調之 Function Calling 模型，以獲得最佳解析效果。3. 從本地編譯安裝 llama-cpp-python目標：讓 llama-cpp-python 從源碼編譯，確保硬體加速與快取控制功能正常。# 3-1) 安裝編譯工具
sudo apt install -y build-essential cmake git

# 3-2) Clone llama-cpp-python（含內建 llama.cpp submodule）
cd ~
git clone --recursive [https://github.com/abetlen/llama-cpp-python.git](https://github.com/abetlen/llama-cpp-python.git)

# 3-3) 從本地編譯安裝（約 5-15 分鐘）
cd ~/llama-cpp-python
CMAKE_ARGS="-DGGML_NATIVE=ON" \
pip install --no-binary :all: --force-reinstall . --break-system-packages
4. 安裝系統# 從電腦傳送專案到 RPI5
scp -r rpi5-demo/ p400@rpi5-demo.local:/home/p400/

# 一鍵安裝（WiFi 熱點 + Python 套件 + 開機自啟）
cd /home/p400/rpi5-demo
sudo bash setup_system.sh
5. HTTPS 憑證設定在 RPI5 上直接執行：# 安裝 certbot
sudo apt update && sudo apt install -y certbot

# 申請憑證（以 DuckDNS 為例）
sudo certbot certonly \
  --manual \
  --preferred-challenges dns \
  --agree-tos \
  --email your@email.com \
  -d rpi5demo.duckdns.org
💡 certbot 暫停時，去 DuckDNS 的 current txt 欄位貼上驗證碼，按 update txt，再回 RPI5 按 Enter。# 設定 nginx + dnsmasq
nano /home/p400/rpi5-demo/setup_https.sh
# 修改 DEMO_DOMAIN="rpi5demo.duckdns.org"
sudo bash setup_https.sh
6. 重開機sudo reboot
📱 訪客使用流程手機連接 WiFi RPI5-Demo（密碼：demo1234）掃描大螢幕上的 QR Code，或開啟 https://rpi5demo.duckdns.org允許麥克風權限點擊麥克風開始語音對話，或直接打字輸入⚠️ 連線 WiFi 時，若手機出現「無網際網路連線」提示，請務必選擇**「保持連線」**。🎨 支援的展示指令（Function Call）本展示系統已內建強效過濾器，支援以下互動：指令意圖觸發效果範例說法改變背景顏色大螢幕背景漸變，手機端廣播「把背景改成紅色」、「換成橘色」查詢城市天氣回傳天氣資料並推播至大螢幕「竹東天氣如何？」、「高雄現在幾度」改變 App 標題模擬 UI 變更「把標題改成你好」顯示警告視窗模擬 Alert 對話框「顯示一個警告：歡迎光臨」支援的顏色清單：紅 (red)、綠 (green)、藍 (blue)、黃 (yellow)、紫 (purple)、橘 (orange)、黑 (black)、白 (white)。(系統具備混色防呆機制，輸入「橘紅色」等複合詞將會被安全攔截。)⚙️ 服務管理# 重啟 AI 服務
sudo systemctl restart rpi5-demo

# 查看即時 Log (可觀看守門員機制的攔截紀錄)
sudo journalctl -u rpi5-demo -f

# 開啟展示螢幕（Kiosk 模式，在 RPI5 桌面執行）
chromium-browser --kiosk --incognito http://localhost:8000/display

# 手動 Debug 測試（先停止背景服務）
sudo systemctl stop rpi5-demo
python3 server.py
🔧 環境變數變數預設值說明PORT8000監聽埠MODEL_PATH自動偵測指定 GGUF 絕對路徑EXTERNAL_URL自動偵測 IP系統對外的 URL (QR Code 使用)N_THREADS4綁定 CPU 執行緒數TEMPERATURE0.7推理溫度，平衡穩定性與語意理解HOTSPOT_SSIDRPI5-Demo展示用 WiFi 名稱HOTSPOT_PASSdemo1234展示用 WiFi 密碼📋 相關文件操作說明書_v2.md：完整安裝與操作說明（9章 + 附錄）RPI5_指令速查表.txt：所有指令一覽，可直接貼到終端機📄 LicenseMIT