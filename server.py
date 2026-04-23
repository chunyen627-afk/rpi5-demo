"""
RPI5 Exhibition Demo — Gemma Function Calling Server (Final Final Stable Version)
═════════════════════════════════════════════════════
模型格式：Google Gemma function calling
推理方式：llama-cpp-python raw completion（自行建構 Gemma 格式 prompt）

🚀 核心優化與修復：
1. 🛡️ 混合色拒絕 (Mixed Color Rejection): 偵測如「橘紅色」等多重顏色，主動拒絕並引導。
2. 🧩 長度優先匹配 (Length Priority): Fast Path 會選取最長的關鍵字，防止 "red" 截胡 "orange"。
   🔥 最新修復：修正字典覆寫問題，確保保留該顏色的「最大長度」。
3. 🧩 KV Cache Reset: 每次推理前強制清除 LLM 底層記憶，防止語境殘留。
4. 🛡️ 守門員驗證 (validate_llm_output): 後驗檢查，只要 Fast Path 抓對就覆蓋 LLM 的錯誤。
5. ⚙️ Temperature 0.7: 平衡格式穩定性與語意理解。

支援的 Function Call：
  change_background_color → 真實改變 RPI5 終端機背景顏色（ANSI）+ 廣播展示螢幕
  get_current_weather     → 回傳假天氣資料（模擬全台灣縣市）
"""

import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import asyncio
import qrcode
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,   # INFO 讓 FAST PATH / Function call 等訊息可見
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("demo")

# ─── Config ───────────────────────────────────────────────
BASE_DIR          = Path(__file__).parent
MODELS_DIR        = BASE_DIR / "models"
SYSTEM_PROMPT_FILE= BASE_DIR / "system_prompt.txt"
PORT              = int(os.getenv("PORT",         "8000"))
EXTERNAL_URL      = os.getenv("EXTERNAL_URL",     "")
N_THREADS         = int(os.getenv("N_THREADS",    "4"))
N_CTX             = int(os.getenv("N_CTX",        "2048"))
MAX_TOKENS        = int(os.getenv("MAX_TOKENS",   "512"))

# ⚠️ 優化：調整 Temperature 為 0.7（Function Calling 需要較高的一致性，避免亂猜顏色）
TEMPERATURE       = float(os.getenv("TEMPERATURE", "0.7"))

# Gemma stop tokens
GEMMA_STOP = ["<end_of_turn>", "<eos>", "<|end|>", "<end_function_call>", "<start_function_response>"]

# 熱點資訊（顯示在展示螢幕頁面）
HOTSPOT_SSID = os.getenv("HOTSPOT_SSID", "RPI5-Demo")
HOTSPOT_PASS = os.getenv("HOTSPOT_PASS", "demo1234")

# 使用說明（當輸入不相關時顯示）
USAGE_GUIDE = (
    "我目前支援兩種指令：\n"
    "\n"
    "🎨 改變背景顏色\n"
    "　範例：「改成紅色」、「藍色」、「換成綠色」\n"
    "　支援：紅、綠、藍、黃、紫、橘、黑、白\n"
    "\n"
    "🌤️ 查詢城市天氣\n"
    "　範例：「台北天氣」、「高雄氣溫」、「竹東現在幾度」\n"
    "\n"
    "請試試以上指令！"
)

# 連線計數（展示螢幕顯示用）
active_connections: int = 0

# ── 並發控制 ──────────────────────────────────────────────
llm_lock   = asyncio.Lock()
queue_size = 0
all_sockets:     set[WebSocket] = set()
display_sockets: set[WebSocket] = set()
current_color: str = ""

async def broadcast(msg: dict, exclude: WebSocket | None = None):
    """廣播訊息給所有連線中的訪客（可排除發送者）"""
    dead = set()
    for ws in all_sockets:
        if ws is exclude:
            continue
        try:
            await ws.send_text(__import__('json').dumps(msg, ensure_ascii=False))
        except Exception:
            dead.add(ws)
    all_sockets.difference_update(dead)

# ─── 支援的顏色名稱 ──────────────────────────────────────
ANSI_COLORS = {"red", "green", "blue", "yellow", "purple", "orange", "black", "white"}

# ─── Load system_prompt.txt ───────────────────────────────
def load_system_prompt() -> str:
    if SYSTEM_PROMPT_FILE.exists():
        content = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
        log.info(f"載入系統提示：{SYSTEM_PROMPT_FILE}")
        return content
    log.warning(f"找不到 {SYSTEM_PROMPT_FILE}，使用預設提示")
    return (
        "<start_of_turn>developer\n"
        "You are a helpful AI assistant.\n"
        "<end_of_turn>"
    )

# ─── Gemma Prompt Builder ─────────────────────────────────
def build_prompt(system_prompt: str, history: list[dict]) -> str:
    """組合 Gemma 格式的完整 prompt"""
    prompt = system_prompt
    if not prompt.endswith("\n"):
        prompt += "\n"

    for msg in history:
        role    = msg["role"]
        content = msg.get("content", "") or ""
        if role == "user":
            prompt += f"<start_of_turn>user\n{content}\n<end_of_turn>\n"
        elif role in ("model", "assistant"):
            prompt += f"<start_of_turn>model\n{content}\n<end_of_turn>\n"
        elif role == "tool":
            prompt += f"<start_of_turn>tool\n{content}\n<end_of_turn>\n"

    # 開啟 model 回應
    prompt += "<start_of_turn>model\n"
    return prompt

# ─── Function Call Parser ─────────────────────────────────
def parse_function_call(text: str) -> tuple[str, dict] | None:
    """解析 Gemma 輸出的 function call"""
    import re
    
    # 1. 清理 Markdown 標記 (如果模型加了 ```python ... ```)
    text = re.sub(r'^```(?:python)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text.strip())

    # 2. 嘗試直接解析 JSON (最穩的方式)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            name = parsed.get("name") or parsed.get("function_name")
            args = parsed.get("arguments", {})
            if name and args:
                return name, args
    except json.JSONDecodeError:
        pass

    # 3. 備用：Regex 解析 (適應官方微調格式)
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}", text, flags=re.DOTALL)
    if m:
        func_name = m.group(1)
        body = m.group(2)
        args = {}
        
        # 解析 key:<escape>value<escape>
        for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*<escape>([^<]*)<escape>", body):
            args[km.group(1)] = km.group(2).strip()
            
        # 解析普通數值 key: value
        for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(true|false|\d+)", body):
             k, v = km.group(1), km.group(2)
             if k not in args:
                 try:
                     args[k] = int(v) if v.isdigit() else v == "true"
                 except: pass
        
        return func_name, args

    return None

def is_function_call(text: str) -> bool:
    """判斷輸出是否包含 function call"""
    known_funcs = {
        "change_background_color",
        "change_app_title",
        "show_alert",
        "get_current_weather",
    }
    for fn in known_funcs:
        if fn in text:
            return True
    return False

# ─── 關鍵字定義 (Fast Path 使用) ──────────────────────────
COLOR_KEYWORDS = {
    "red":    ["red", "紅色", "紅", "red color"],
    "green":  ["green", "綠色", "綠", "green color"],
    "blue":   ["blue", "藍色", "藍", "blue color"],
    "yellow": ["yellow", "黃色", "黃", "yellow color"],
    "purple": ["purple", "紫色", "紫", "violet"],
    "orange": ["orange", "橘色", "橘", "橙色", "橙"],
    "black":  ["black", "黑色", "黑"],
    "white":  ["white", "白色", "白"],
}

CITY_KEYWORDS = {
    "台北": ["台北", "taipei", "臺北"],
    "新北": ["新北", "new taipei", "板橋"],
    "桃園": ["桃園", "taoyuan"],
    "基隆": ["基隆", "keelung"],
    "新竹": ["新竹", "hsinchu", "竹北"],
    "苗栗": ["苗栗", "miaoli"],
    "台中": ["台中", "taichung", "臺中"],
    "彰化": ["彰化", "changhua"],
    "南投": ["南投", "nantou"],
    "雲林": ["雲林", "yunlin"],
    "嘉義": ["嘉義", "chiayi", "嘉市"],
    "台南": ["台南", "tainan", "臺南"],
    "高雄": ["高雄", "kaohsiung"],
    "屏東": ["屏東", "pingtung"],
    "澎湖": ["澎湖", "penghu"],
    "花蓮": ["花蓮", "hualien"],
    "台東": ["台東", "taitung"],
    "金門": ["金門", "kinmen"],
    "馬祖": ["馬祖", "lienchiang", "連江"],
    "tokyo": ["tokyo", "東京"],
    "osaka": ["osaka", "大阪"],
}

WEATHER_INDICATORS = ["天氣", "氣溫", "氣候", "溫度", "下雨", "晴", "陰", "降雨",
                      "weather", "temperature", "climate", "hot", "cold", "rain",
                      "熱", "冷", "幾度", "度"]

COLOR_VERBS = ["改", "換", "變", "設定", "背景", "change", "background", "switch"]

def extract_location_from_text(text: str) -> str:
    """從文字萃取地點名稱"""
    cleaned = text
    for w in WEATHER_INDICATORS:
        cleaned = cleaned.replace(w, "")
    for w in ["現在", "今天", "目前", "如何", "怎樣", "呢", "嗎", "？", "?", "的"]:
        cleaned = cleaned.replace(w, "")
    return re.sub(r'\s+', '', cleaned).strip()

def fast_path_intent(user_text: str) -> tuple[str, dict] | None:
    """
    關鍵字分類器：針對「展示用」的兩個工具，用簡單規則快速判斷意圖。
    🛡️ 優化：增加「單一顏色檢查」與「長度優先匹配（保留最長）」。
    """
    text = user_text.lower().strip()
    if not text:
        return None

    # 1. 找出所有出現的關鍵字及其對應的標準顏色
    found_colors_map = {} 
    for canon, kws in COLOR_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                # 🚀 修正：只有當目前沒有記錄，或是新抓到的關鍵字比舊的更長時，才更新
                # 防止 "red" (3) 被 "紅" (1) 覆寫
                if canon not in found_colors_map or len(kw) > found_colors_map[canon]:
                    found_colors_map[canon] = len(kw)

    # 2. 檢查是否有多個不同的顏色（混合色檢測）
    unique_found_colors = list(found_colors_map.keys())
    
    # 🛡️ 嚴格檢查：如果發現超過一個支援的顏色（例如「橘紅色」= 橘 + 紅）
    if len(unique_found_colors) > 1:
        log.warning(f"[拒絕] 輸入包含多個顏色 {unique_found_colors}，判定為混合色不支援")
        return ("__reject__", {"message": "抱歉，目前僅支援單一顏色：紅、綠、藍、黃、紫、橘。請不要輸入混合色（如橘紅色）。"})

    # 3. 如果只抓到一個顏色，進行長度優先匹配
    found_color = None
    if unique_found_colors:
        # 因為前面已經確保 found_colors_map 裡存的是該顏色的「最大長度」，這裡直接取出來排序即可
        matched_colors = [(found_colors_map[c], c) for c in unique_found_colors]
        matched_colors.sort(key=lambda x: x[0], reverse=True)
        found_color = matched_colors[0][1]

    # --- (以下邏輯維持不變) ---
    found_city = None
    for canon, kws in CITY_KEYWORDS.items():
        if any(kw.lower() in text for kw in kws):
            found_city = canon
            break

    has_weather_hint = any(w.lower() in text for w in WEATHER_INDICATORS)
    has_color_verb   = any(v.lower() in text for v in COLOR_VERBS)

    if has_weather_hint and not has_color_verb:
        location = found_city or extract_location_from_text(user_text)
        return ("get_current_weather", {"location": location})

    # 只有顏色且無其他干擾時才執行改顏色
    if found_color and not found_city and not has_weather_hint:
        return ("change_background_color", {"color": found_color})

    # 城市判斷邏輯...
    if found_city and not found_color and not has_weather_hint:
        return None 

    if found_color and found_city:
        if has_weather_hint:
            return ("get_current_weather", {"location": found_city})
        if has_color_verb:
            return ("change_background_color", {"color": found_color})
    
    return None

# ─── 守門員驗證 (Post-Validation) ─────────────────────────
def validate_llm_output(user_text: str, func_name: str, func_args: dict) -> tuple[str, dict]:
    """
    【守門員模式】：模型先跑，這裡負責「糾正」並「嚴格審查」。
    邏輯：只要 Fast Path 抓不到（None），代表輸入不標準，直接拒絕 LLM 的執行。
    """
    text = user_text.lower().strip()

    # 1. 執行 Fast Path 分析（作為標準答案對照組）
    hint = fast_path_intent(user_text)
    
    # ── 🔥 核心修正：攔截未定義的輸入 (嚴格守門員) ──
    if hint is None:
        log.warning(f"[嚴格攔截] Fast Path 未抓到關鍵字 (hint=None)，拒絕執行 {func_name}。")
        return ("__usage__", {})

    # 🛡️ 新增：如果 Fast Path 判定為「混合色」或其他拒絕原因
    if hint[0] == "__reject__":
        log.warning(f"[拒絕執行] {hint[1].get('message')}")
        return ("__reject__", hint[1])

    hinted_name, hinted_args = hint
    
    # ── 情況 A：意圖一致，修正參數（防止 LLM 說橙色卻輸出紅色）──
    if hinted_name == func_name:
        log.info(f"[守門員] LLM 與關鍵字意圖一致 ({func_name})，正在檢查參數...")

        # 🔥 針對改顏色：強制修正參數
        if func_name == "change_background_color":
            llm_color = func_args.get("color", "").lower()
            target_color = hinted_args.get("color", "").lower()
            
            if target_color and target_color != llm_color:
                log.warning(f"[強制修正] 用戶輸入 '{user_text}' -> 目標顏色 {target_color}，LLM 誤判為 {llm_color}。已強制修正。")
                func_args = {"color": target_color}
        
        # 🔥 針對查天氣：強制修正城市
        elif func_name == "get_current_weather":
            llm_city = func_args.get("location", "").lower()
            target_city = hinted_args.get("location", "").lower()
            
            if target_city and target_city != llm_city:
                log.warning(f"[強制修正] LLM 誤判城市為 {llm_city}，根據關鍵字改為 {target_city}")
                func_args = {"location": target_city}

    # ── 情況 B：意圖不同（例如說橙色，LLM 卻要查天氣）──
    else:
        log.warning(f"[守門員] LLM 判 {func_name}, 但關鍵字判 {hinted_name}。")
        
        # 只要 Fast Path 抓到了明確的顏色或城市，優先信任關鍵字（穩定第一）
        if hinted_name == "change_background_color":
             log.info("[強制覆蓋] 關鍵字判定為改顏色，覆蓋 LLM 的錯誤意圖")
             return (hinted_name, hinted_args)
             
        if hinted_name == "get_current_weather":
             log.info("[強制覆蓋] 關鍵字判定為查天氣，覆蓋 LLM 的錯誤意圖")
             return (hinted_name, hinted_args)

    return (func_name, func_args)

# ─── Tool Implementations ─────────────────────────────────

def tool_change_background_color(color: str) -> tuple[str, str]:
    """改變 /display 展示螢幕頁面的背景顏色"""
    global current_color
    color = color.lower().strip()
    if color not in ANSI_COLORS:
        return (f"[未知顏色: {color}]", f"錯誤：不支援的顏色 '{color}'，支援：{', '.join(ANSI_COLORS)}")

    current_color = color
    log_msg = f"[顏色事件] 展示頁面背景 → {color}"
    log.warning(log_msg)

    import asyncio as _asyncio
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_push_color_to_display(color))
    except Exception as e:
        log.warning(f"推送顏色事件失敗: {e}")

    result_msg = f"展示螢幕背景顏色已成功變更為 {color}"
    return (log_msg, result_msg)


async def _push_color_to_display(color: str):
    """推送顏色變更事件給所有 /ws/display 連線"""
    dead = set()
    msg  = json.dumps({"type": "color_change", "color": color}, ensure_ascii=False)
    for ws in display_sockets:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    display_sockets.difference_update(dead)


async def _push_result_to_display(title: str, text: str):
    """推送工具執行結果（文字卡片）給所有 /ws/display 連線"""
    dead = set()
    msg  = json.dumps({"type": "result", "title": title, "text": text}, ensure_ascii=False)
    for ws in display_sockets:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    display_sockets.difference_update(dead)


def tool_get_current_weather(location: str) -> tuple[str, str]:
    fake_weather = {
        # 🏙️ 北部
        "台北": {"temp": "28°C", "condition": "晴天", "humidity": "75%"},
        "新北": {"temp": "29°C", "condition": "多雲有陽光", "humidity": "78%"},
        "桃園": {"temp": "30°C", "condition": "局部陣雨", "humidity": "70%"},
        "基隆": {"temp": "26°C", "condition": "陰天", "humidity": "88%"},
        "新竹": {"temp": "29°C", "condition": "晴天", "humidity": "65%"},
        "苗栗": {"temp": "28°C", "condition": "陰天多雲", "humidity": "72%"},
        
        # 🌾 中部
        "台中": {"temp": "30°C", "condition": "多雲", "humidity": "68%"},
        "彰化": {"temp": "31°C", "condition": "晴天", "humidity": "64%"},
        "南投": {"temp": "27°C", "condition": "局部雷陣雨", "humidity": "80%"},
        "雲林": {"temp": "30°C", "condition": "多雲有陽光", "humidity": "70%"},
        "嘉義": {"temp": "31°C", "condition": "晴天", "humidity": "62%"},
        
        # 🌊 南部
        "台南": {"temp": "32°C", "condition": "局部陣雨", "humidity": "78%"},
        "高雄": {"temp": "33°C", "condition": "晴天", "humidity": "75%"},
        "屏東": {"temp": "34°C", "condition": "炎熱多雲", "humidity": "82%"},
        "澎湖": {"temp": "28°C", "condition": "多雲有陽光", "humidity": "72%"},
        
        # 🏔️ 東部與離島
        "花蓮": {"temp": "29°C", "condition": "局部陣雨", "humidity": "76%"},
        "台東": {"temp": "30°C", "condition": "晴天", "humidity": "70%"},
        "金門": {"temp": "29°C", "condition": "晴朗微風", "humidity": "68%"},
        "馬祖": {"temp": "25°C", "condition": "涼爽多雲", "humidity": "80%"},
        
        # 🌏 國際參考（保留原設定）
        "tokyo": {"temp": "22°C", "condition": "Cloudy", "humidity": "65%"},
        "osaka": {"temp": "24°C", "condition": "小雨", "humidity": "78%"},
    }
    
    loc = location.strip()
    if loc in fake_weather:
        w = fake_weather[loc]
        result = f"{loc} 目前天氣：{w['condition']}，氣溫 {w['temp']}"
        # 推送到展示螢幕
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_push_result_to_display(f"🌤️ {loc} 天氣", result))
        except Exception as e:
            log.warning(f"推送天氣事件失敗: {e}")
        return (f"[WEATHER] {loc} 成功", result)
    else:
        # 🛡️ 防線：非支援城市直接讓 AI 認輸
        return (f"[WEATHER] 找不到 {loc}", f"抱歉，我目前沒有「{loc}」的氣象資料。")


def tool_change_app_title(title: str) -> tuple[str, str]:
    msg = f"\n[DEMO] change_app_title → 標題已變更為：「{title}」\n"
    sys.stdout.write(f"\033[36m{msg}\033[0m")
    sys.stdout.flush()
    return (msg, f"App 標題已成功變更為「{title}」")

def tool_show_alert(title: str, message: str) -> tuple[str, str]:
    box = (
        f"\n\033[33m┌─ ALERT ────────────────────────────────┐\n"
        f"│ 標題：{title:<36}│\n"
        f"│ 訊息：{message:<36}│\n"
        f"└────────────────────────────────────────┘\033[0m\n"
    )
    sys.stdout.write(box)
    sys.stdout.flush()
    return (box, f"已顯示 Alert：標題「{title}」，訊息「{message}」")

# 函數分發表
TOOL_DISPATCH = {
    "change_background_color": lambda a: tool_change_background_color(a.get("color", "")),
    "change_app_title":        lambda a: tool_change_app_title(a.get("title", "")),
    "show_alert":              lambda a: tool_show_alert(a.get("title",""), a.get("message","")),
    "get_current_weather":     lambda a: tool_get_current_weather(a.get("location", "")),
}

def execute_tool(func_name: str, args: dict) -> tuple[str, str]:
    fn = TOOL_DISPATCH.get(func_name)
    if fn:
        return fn(args)
    msg = f"[未知函數: {func_name}]"
    return (msg, msg)

# ─── Helpers ──────────────────────────────────────────────
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "192.168.4.1"
    finally:
        s.close()

def get_url() -> str:
    return EXTERNAL_URL or f"http://{get_local_ip()}:{PORT}"

def find_gguf() -> str:
    explicit = os.getenv("MODEL_PATH", "")
    if explicit and Path(explicit).exists():
        return explicit
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MODELS_DIR.glob("*.gguf"))
    if files:
        return str(files[0])
    raise FileNotFoundError(f"\n  ❌ 找不到 GGUF 模型！請將 .gguf 放入：{MODELS_DIR}/\n")

def load_model():
    from llama_cpp import Llama
    path = find_gguf()
    log.info(f"載入模型：{path}")
    llm = Llama(
        model_path=path,
        n_ctx=N_CTX,
        n_threads=N_THREADS,
        n_gpu_layers=0,
        verbose=False,
    )
    log.info("模型就緒 ✓")
    return llm, path

# ─── FastAPI ──────────────────────────────────────────────
app = FastAPI()
LLM: object         = None
MODEL_FILE: str     = ""
SYSTEM_PROMPT: str  = ""

@app.on_event("startup")
async def startup():
    global LLM, MODEL_FILE, SYSTEM_PROMPT
    LLM, MODEL_FILE = load_model()
    SYSTEM_PROMPT   = load_system_prompt()
    log.info(f"URL: {get_url()}")

@app.get("/")
async def index():
    return HTMLResponse((BASE_DIR / "templates" / "index.html").read_text("utf-8"))

@app.get("/display")
async def display():
    return HTMLResponse((BASE_DIR / "templates" / "display.html").read_text("utf-8"))

@app.get("/qr.png")
async def get_qr():
    """生成標準 URL QR Code"""
    url = get_url()
    qr  = qrcode.QRCode(version=1, box_size=10, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#000000", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")

@app.get("/info")
async def info():
    return {
        "url":       get_url(),
        "model":     Path(MODEL_FILE).name if MODEL_FILE else "none",
        "ip":        get_local_ip(),
        "ssid":      HOTSPOT_SSID,
        "wifi_pass": HOTSPOT_PASS,
    }

@app.get("/stats")
async def stats():
    temp = uptime = "—"
    try:
        raw  = subprocess.check_output("cat /sys/class/thermal/thermal_zone0/temp", shell=True, text=True, timeout=2, stderr=subprocess.DEVNULL).strip()
        temp = f"{int(raw)/1000:.1f}°C" if raw.isdigit() else raw
    except Exception: pass
    try:
        uptime = subprocess.check_output("uptime -p", shell=True, text=True, timeout=2, stderr=subprocess.DEVNULL).strip().replace("up ", "")
    except Exception: pass
    return {"temp": temp, "uptime": uptime, "conns": active_connections}

@app.websocket("/ws/display")
async def ws_display(ws: WebSocket):
    await ws.accept()
    display_sockets.add(ws)
    log.warning(f"Display 連線（共 {len(display_sockets)} 個展示頁面）")
    if current_color:
        await ws.send_text(json.dumps({"type": "color_change", "color": current_color}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        display_sockets.discard(ws)

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    global active_connections, queue_size
    await ws.accept()
    active_connections += 1
    all_sockets.add(ws)

    session_id = active_connections
    log.info(f"新連線 #{session_id}（目前 {active_connections} 人）")
    history: list[dict] = []

    async def send(obj: dict):
        await ws.send_text(json.dumps(obj, ensure_ascii=False))

    try:
        while True:
            raw  = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") != "chat": continue

            user_text = data.get("text", "").strip()
            if not user_text or LLM is None: continue

            # 🚀 每一句話開始處理前，清空歷史紀錄（確保單次互動穩定）
            history = []
            
            log.info(f"User: {user_text}")
            history.append({"role": "user", "content": user_text})

            # ── 建構 Gemma Prompt ────────────────────────
            prompt = build_prompt(SYSTEM_PROMPT, history)

            # ── 排隊取得推理鎖 ────────────────────────────
            queue_size += 1
            is_waiting = True

            try:
                wait_pos = queue_size
                if wait_pos > 1:
                    await send({"type": "status", "text": f"前面還有 {wait_pos - 1} 人，請稍候..."})

                async with llm_lock:
                    queue_size -= 1
                    is_waiting = False
                    if wait_pos > 1:
                        await send({"type": "status", "text": "輪到你了，推理中..."})

                    # 🧩 補上這兩行！強制清除 LLM 的底層記憶殘留 (KV Cache)
                    if hasattr(LLM, "reset"):
                        LLM.reset()

                    try:
                        r1 = await asyncio.to_thread(
                            LLM, prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, stop=GEMMA_STOP, echo=False
                        )
                    except Exception as e:
                        log.error(f"LLM error: {e}")
                        await send({"type": "error", "text": "推理失敗，請重試"})
                        continue

                    output1 = r1["choices"][0]["text"].strip()
                    log.info(f"Model output: {output1[:120]}")

                    # ── 判斷是否有 Function Call ─────────────
                    if is_function_call(output1):
                        parsed = parse_function_call(output1)

                        if parsed:
                            func_name, func_args = parsed
                            
                            # ══ 守門員驗證：後驗檢查 ══
                            func_name, func_args = validate_llm_output(user_text, func_name, func_args)

                            # ── 處理混合色拒絕 ──
                            if func_name == "__reject__":
                                reply_msg = func_args.get("message", "輸入顏色有衝突，請重新輸入。")
                                log.info(f"[__reject__] {reply_msg}")
                                for char in reply_msg: await send({"type": "token", "text": char}); await asyncio.sleep(0.02)
                                await send({"type": "done"}); continue

                            # ── 拒絕或模糊輸入 ──
                            if func_name == "__usage__":
                                log.info("[__usage__] 輸入不標準，顯示使用說明")
                                for char in USAGE_GUIDE: await send({"type": "token", "text": char}); await asyncio.sleep(0.02)
                                await send({"type": "done"}); continue

                            log.info(f"Function call: {func_name}({func_args})")
                            await send({"type": "status", "text": f"執行 {func_name}..."})

                            # 執行工具
                            terminal_out, result_msg = execute_tool(func_name, func_args)
                            log.info(f"Tool result: {result_msg}")

                            if func_name == "change_background_color":
                                color = func_args.get("color", "")
                                await broadcast({
                                    "type":  "broadcast",
                                    "text":  f"訪客 #{session_id} 把螢幕顏色改成了 {color}",
                                    "color": color,
                                }, exclude=ws)

                            # 記錄歷史（讓模型記住剛才發生了什麼）
                            history.append({"role": "model", "content": output1})
                            history.append({
                                "role": "tool",
                                "content": json.dumps([{"output": result_msg}], ensure_ascii=False)
                            })

                            final_text = result_msg
                            for char in final_text: await send({"type": "token", "text": char}); await asyncio.sleep(0.02)
                            await send({"type": "done"})
                            
                            history.append({"role": "model", "content": final_text})

                        else:
                            # 解析失敗，顯示說明
                            log.warning(f"Function call 解析失敗。原始輸出: {output1[:80]}")
                            for char in USAGE_GUIDE: await send({"type": "token", "text": char}); await asyncio.sleep(0.02)
                            await send({"type": "done"}); continue

                    else:
                        # LLM 沒有輸出 function call → 顯示說明
                        log.info(f"LLM 無 function call，原始輸出: {output1[:80]}")
                        for char in USAGE_GUIDE: await send({"type": "token", "text": char}); await asyncio.sleep(0.02)
                        await send({"type": "done"}); continue

                    # ── 歷史管理 ────────────
                    if len(history) > 20: history = history[-20:]

            finally:
                if is_waiting:
                    queue_size = max(0, queue_size - 1)

    except WebSocketDisconnect:
        active_connections = max(0, active_connections - 1)
        all_sockets.discard(ws)
    except Exception as e:
        log.error(f"WS 錯誤: {e}", exc_info=True)

if __name__ == "__main__":
    url = get_url()
    print("\n" + "═"*56)
    print(f"  RPI5 AI 展示伺服器 (Gemma Function Calling)")
    print(f"  訪客連線  : {url}")
    print(f"  展示螢幕  : {url}/display")
    print(f"  QR Code  : {url}/qr.png")
    print(f"  WiFi     : {HOTSPOT_SSID} / {HOTSPOT_PASS}")
    print("═"*56 + "\n")
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, log_level="warning", reload=False)