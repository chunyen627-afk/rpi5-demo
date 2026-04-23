#!/bin/bash
# ============================================================
# rebuild_llama.sh (強化版)
#
# 用途：修改 ~/llama-cpp-python/vendor/llama.cpp/ 源碼後，
#       重新編譯並安裝 llama-cpp-python，確保 Python 吃到最新 C++ 引擎。
#
# 特色：加入多核心平行編譯加速、徹底清除舊版快取、消除 pip 警告。
# ============================================================

set -e

LLAMA_CPP_PY_DIR="$HOME/llama-cpp-python"

# ── Step 1: 檢查專案目錄存在 ──────────────────────────────
echo "▶ [1/5] 檢查 llama-cpp-python 目錄"
if [ ! -d "$LLAMA_CPP_PY_DIR" ]; then
    echo "  ❌ 找不到 $LLAMA_CPP_PY_DIR"
    echo "     請先執行："
    echo "       cd ~ && git clone --recursive https://github.com/abetlen/llama-cpp-python.git"
    exit 1
fi
if [ ! -d "$LLAMA_CPP_PY_DIR/vendor/llama.cpp" ]; then
    echo "  ❌ vendor/llama.cpp 不存在！submodule 沒抓下來"
    echo "     請執行："
    echo "       cd $LLAMA_CPP_PY_DIR && git submodule update --init --recursive"
    exit 1
fi
echo "  ✓ 目錄正常"

# ── Step 2: 顯示當前 llama.cpp 版本 ───────────────────────
echo ""
echo "▶ [2/5] 檢查 llama.cpp 版本與修改狀態"
cd "$LLAMA_CPP_PY_DIR/vendor/llama.cpp"
CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "無 git 資訊")
echo "  vendor/llama.cpp 版本：$CURRENT_COMMIT"
MODIFIED=$(git status --short 2>/dev/null | wc -l)
if [ "$MODIFIED" -gt 0 ]; then
    echo "  ⚠️  偵測到 $MODIFIED 個未提交的修改："
    git status --short | head -10
fi

# ── Step 3: 停止 rpi5-demo 釋放 .so ──────────────────────
echo ""
echo "▶ [3/5] 暫停 rpi5-demo 服務"
if systemctl is-active rpi5-demo &>/dev/null; then
    sudo systemctl stop rpi5-demo
    echo "  ✓ 已停止 rpi5-demo"
else
    echo "  ✓ rpi5-demo 未在執行中"
fi

# ── Step 4: 徹底淨空舊版環境 (極度重要) ───────────────────
echo ""
echo "▶ [4/5] 清除舊版套件與快取"
# 使用 || true 防止因為原本就沒安裝而導致腳本中斷
pip3 uninstall llama-cpp-python -y --break-system-packages &>/dev/null || true
pip3 cache purge &>/dev/null
echo "  ✓ 系統暫存與舊版檔案已淨空"

# ── Step 5: 重新編譯 llama-cpp-python ────────────────────
echo ""
echo "▶ [5/5] 多核心平行編譯 llama-cpp-python（火力全開中...）"
cd "$LLAMA_CPP_PY_DIR"

# 魔法參數說明：
# CMAKE_BUILD_PARALLEL_LEVEL=4：呼叫 RPI5 的 4 顆核心同時編譯，大幅省時
# -DGGML_NEON=ON -DGGML_NATIVE=ON：確保 ARM 專屬硬體加速開啟
# --no-cache-dir：避免吃到舊版暫存檔
CMAKE_ARGS="-DGGML_NEON=ON -DGGML_NATIVE=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF" \
CMAKE_BUILD_PARALLEL_LEVEL=4 \
pip3 install --no-cache-dir --force-reinstall . --break-system-packages

echo ""
echo "╔════════════════════════════════════════╗"
echo "║  🚀 編譯與安裝完美結束！                  ║"
echo "╠════════════════════════════════════════╣"
echo "║  重啟服務：                             ║"
echo "║    sudo systemctl start rpi5-demo      ║"
echo "║                                        ║"
echo "║  查看 Log 確認生效：                    ║"
echo "║    sudo journalctl -u rpi5-demo -f     ║"
echo "╚════════════════════════════════════════╝"