"""
RPI5 Exhibition Demo — Gemma Function Calling Server
═════════════════════════════════════════════════════
模型格式：Google Gemma function calling
系統提示：從 system_prompt.txt 載入（<start_of_turn>developer 格式）
推理方式：llama-cpp-python raw completion（自行建構 Gemma 格式 prompt）

支援的 Function Call：
  change_background_color → 真實改變 RPI5 終端機背景顏色（ANSI）
  change_app_title        → 印出訊息（模擬）
  show_alert              → 印出訊息（模擬）
  get_current_weather     → 回傳假天氣資料（模擬）

Gemma Function Calling 格式：
  輸入：<start_of_turn>developer ... <end_of_turn>
        <start_of_turn>user ... <end_of_turn>
        <start_of_turn>model
  輸出：```python\nfunction_name(arg="val")\n```
  工具回應：<start_of_turn>tool\n[{"output":"..."}]\n<end_of_turn>
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
    level=logging.WARNING,  # 展出時用 WARNING，減少 SD 卡 IO
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
TEMPERATURE       = float(os.getenv("TEMPERATURE","0.7"))

# Gemma stop tokens
GEMMA_STOP = ["<end_of_turn>", "<eos>", "<|end|>"]

# 熱點資訊（顯示在展示螢幕頁面）
HOTSPOT_SSID = os.getenv("HOTSPOT_SSID", "RPI5-Demo")
HOTSPOT_PASS = os.getenv("HOTSPOT_PASS", "demo1234")

# 連線計數（展示螢幕顯示用）
active_connections: int = 0

# ── 並發控制 ──────────────────────────────────────────────
# LLM 不是 thread-safe，同時只能跑一個推理
llm_lock   = asyncio.Lock()
queue_size = 0                          # 目前等待推理的請求數
all_sockets:     set[WebSocket] = set() # 訪客 WebSocket（廣播用）
display_sockets: set[WebSocket] = set() # /display 展示頁面 WebSocket（顏色事件用）
current_color: str = ""                 # 目前展示螢幕顏色

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

# ─── 支援的顏色名稱（/display 頁面用 CSS 顏色，這裡只做輸入驗證）─────
ANSI_COLORS = {"red", "green", "blue", "yellow", "purple", "orange"}

# ─── Load system_prompt.txt ───────────────────────────────
def load_system_prompt() -> str:
    if SYSTEM_PROMPT_FILE.exists():
        content = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
        log.info(f"載入系統提示：{SYSTEM_PROMPT_FILE}")
        return content
    # Fallback if file missing
    log.warning(f"找不到 {SYSTEM_PROMPT_FILE}，使用預設提示")
    return (
        "<start_of_turn>developer\n"
        "You are a helpful AI assistant.\n"
        "<end_of_turn>"
    )

# ─── Gemma Prompt Builder ─────────────────────────────────
def build_prompt(system_prompt: str, history: list[dict]) -> str:
    """
    組合 Gemma 格式的完整 prompt。
    system_prompt 已包含 <start_of_turn>developer ... <end_of_turn>
    history 格式：[{"role": "user"|"model"|"tool", "content": "..."}]
    """
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
    """
    解析 Gemma 輸出的 function call。
    支援官方微調模型的 <start_function_call> 格式，以及 Markdown 備用格式。
    """
    import re
    import json

    content = text
    # 1. 處理官方 <start_function_call> 標籤
    if "<start_function_call>" in content:
        content = content.split("<start_function_call>", 1)[1]
    if "<end_function_call>" in content:
        content = content.split("<end_function_call>", 1)[0]
    content = content.strip()

    if not content:
        return None

    # 2. 嘗試解析 JSON 格式 (有些模型微調後會吐 JSON)
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "name" in parsed:
            return parsed.get("name"), parsed.get("arguments", {}) or {}
    except json.JSONDecodeError:
        pass

    # 3. 嘗試解析官方 declaration 格式: call:func_name{key:<escape>val<escape>}
    m = re.match(
        r"(?:functioncall:|declaration:|call:)?([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*)\}\s*$",
        content,
        flags=re.DOTALL,
    )
    if m:
        func_name = m.group(1)
        body = m.group(2)
        args = {}
        # 提取 key:<escape>value<escape>
        for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*<escape>(.*?)<escape>", body, flags=re.DOTALL):
            args[km.group(1)] = km.group(2)
        # 提取無 escape 的數字或布林值
        for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?\d+(?:\.\d+)?|true|false)\b", body):
            k, v = km.group(1), km.group(2)
            if k not in args:
                if v == "true": args[k] = True
                elif v == "false": args[k] = False
                elif "." in v: args[k] = float(v)
                else: args[k] = int(v)
        return func_name, args

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

# ─── Tool Implementations ─────────────────────────────────

def tool_change_background_color(color: str) -> tuple[str, str]:
    """
    改變 /display 展示螢幕頁面的背景顏色。
    透過 /ws/display WebSocket 推送事件給展示頁面。
    回傳：(log_msg, result_for_model)
    """
    global current_color
    color = color.lower().strip()
    if color not in ANSI_COLORS:
        return (
            f"[未知顏色: {color}]",
            f"錯誤：不支援的顏色 '{color}'，支援：{', '.join(ANSI_COLORS)}"
        )

    current_color = color
    log_msg = f"[顏色事件] 展示頁面背景 → {color}"
    log.warning(log_msg)

    # 非同步推送到所有 /ws/display 連線
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


def tool_change_app_title(title: str) -> tuple[str, str]:
    """模擬：改變 App 標題"""
    msg = f"\n[DEMO] change_app_title → 標題已變更為：「{title}」\n"
    sys.stdout.write(f"\033[36m{msg}\033[0m")
    sys.stdout.flush()
    return (msg, f"App 標題已成功變更為「{title}」")


def tool_show_alert(title: str, message: str) -> tuple[str, str]:
    """模擬：顯示 Alert 對話框"""
    box = (
        f"\n\033[33m┌─ ALERT ────────────────────────────────┐\n"
        f"│ 標題：{title:<36}│\n"
        f"│ 訊息：{message:<36}│\n"
        f"└────────────────────────────────────────┘\033[0m\n"
    )
    sys.stdout.write(box)
    sys.stdout.flush()
    return (box, f"已顯示 Alert：標題「{title}」，訊息「{message}」")


def tool_get_current_weather(location: str) -> tuple[str, str]:
    """模擬：取得天氣資料（假資料）"""
    fake_weather = {
        "台北": {"temp": "28°C", "condition": "晴天", "humidity": "75%"},
        "台中": {"temp": "30°C", "condition": "多雲", "humidity": "68%"},
        "高雄": {"temp": "32°C", "condition": "晴天", "humidity": "72%"},
        "tokyo": {"temp": "22°C", "condition": "Cloudy", "humidity": "65%"},
    }
    w = fake_weather.get(location.lower(),
        {"temp": "25°C", "condition": "晴天", "humidity": "70%"})

    msg = (
        f"\n\033[34m[WEATHER] {location}：{w['temp']} / "
        f"{w['condition']} / 濕度 {w['humidity']}\033[0m\n"
    )
    sys.stdout.write(msg)
    sys.stdout.flush()

    result = (
        f"{location} 目前天氣：{w['condition']}，"
        f"氣溫 {w['temp']}，濕度 {w['humidity']}"
    )
    return (msg, result)


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
    raise FileNotFoundError(
        f"\n  ❌ 找不到 GGUF 模型！\n"
        f"  請將 .gguf 放入：{MODELS_DIR}/\n"
    )

def load_model():
    from llama_cpp import Llama
    path = find_gguf()
    log.info(f"載入模型：{path}")
    log.info(f"threads={N_THREADS}, ctx={N_CTX}")
    llm = Llama(
        model_path=path,
        n_ctx=N_CTX,
        n_threads=N_THREADS,
        n_gpu_layers=0,
        # 不指定 chat_format，用 raw completion 自行管理 Gemma 格式
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
    """展示螢幕頁面：顯示 QR Code + WiFi 資訊，給 RPI5 外接螢幕用"""
    return HTMLResponse((BASE_DIR / "templates" / "display.html").read_text("utf-8"))

@app.get("/qr.png")
async def get_qr():
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
    """系統狀態：溫度、運行時間、連線數（給展示螢幕頁面輪詢用）"""
    temp = uptime = "—"
    try:
        raw  = subprocess.check_output(
            "cat /sys/class/thermal/thermal_zone0/temp",
            shell=True, text=True, timeout=2
        ).strip()
        temp = f"{int(raw)/1000:.1f}°C" if raw.isdigit() else raw
    except Exception:
        pass
    try:
        uptime = subprocess.check_output(
            "uptime -p", shell=True, text=True, timeout=2
        ).strip().replace("up ", "")
    except Exception:
        pass
    return {"temp": temp, "uptime": uptime, "conns": active_connections}


@app.websocket("/ws/display")
async def ws_display(ws: WebSocket):
    """展示螢幕專屬 WebSocket，接收顏色變更事件"""
    await ws.accept()
    display_sockets.add(ws)
    log.warning(f"Display 連線（共 {len(display_sockets)} 個展示頁面）")
    # 新連線立即同步目前顏色
    if current_color:
        await ws.send_text(json.dumps({"type": "color_change", "color": current_color}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        display_sockets.discard(ws)
        log.warning(f"Display 斷線（共 {len(display_sockets)} 個展示頁面）")


# ─── WebSocket ────────────────────────────────────────────
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    global active_connections, queue_size
    await ws.accept()
    active_connections += 1
    all_sockets.add(ws)

    # 給每個連線一個匿名 session 編號（展示用，不記名）
    session_id = active_connections
    log.info(f"新連線 #{session_id}（目前 {active_connections} 人）")
    history: list[dict] = []

    async def send(obj: dict):
        await ws.send_text(json.dumps(obj, ensure_ascii=False))

    try:
        while True:
            raw  = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") != "chat":
                continue

            user_text = data.get("text", "").strip()
            if not user_text or LLM is None:
                continue

            log.info(f"User: {user_text}")
            history.append({"role": "user", "content": user_text})

            # ── 建構 Gemma Prompt ────────────────────────
            prompt = build_prompt(SYSTEM_PROMPT, history)

            # ── 排隊取得推理鎖 ────────────────────────────
            # LLM 非 thread-safe，整個推理流程（含第二次呼叫）都必須在 lock 內
            queue_size += 1
            is_waiting = True   # 旗標：是否仍在等待鎖（用於 finally 修正計數）

            try:
                wait_pos = queue_size
                if wait_pos > 1:
                    await send({
                        "type": "status",
                        "text": f"前面還有 {wait_pos - 1} 人，請稍候..."
                    })
                    log.info(f"連線 #{session_id} 排隊等待，位置 {wait_pos}")

                async with llm_lock:
                    # ── 取得鎖，開始推理 ─────────────────────
                    queue_size -= 1
                    is_waiting = False   # 已取得鎖，不再是「等待中」狀態
                    if wait_pos > 1:
                        await send({"type": "status", "text": "輪到你了，推理中..."})
                    log.info(f"連線 #{session_id} 開始推理（佇列剩 {queue_size} 人）")

                    # ── 第一次推理（asyncio.to_thread 避免阻塞 Event Loop）────
                    try:
                        r1 = await asyncio.to_thread(
                            LLM,
                            prompt,
                            max_tokens=MAX_TOKENS,
                            temperature=TEMPERATURE,
                            stop=GEMMA_STOP,
                            echo=False,
                        )
                    except Exception as e:
                        log.error(f"LLM error: {e}")
                        await send({"type": "error", "text": "推理失敗，請重試"})
                        continue

                    output1 = r1["choices"][0]["text"].strip()
                    log.info(f"Model output: {output1[:100]}")

                    # ── 判斷是否有 Function Call ─────────────
                    if is_function_call(output1):
                        parsed = parse_function_call(output1)

                        if parsed:
                            func_name, func_args = parsed
                            log.info(f"Function call: {func_name}({func_args})")
                            await send({"type": "status", "text": f"執行 {func_name}..."})

                            # 執行工具
                            terminal_out, result_msg = execute_tool(func_name, func_args)
                            log.info(f"Tool result: {result_msg}")

                            # change_background_color：廣播給所有訪客
                            if func_name == "change_background_color":
                                color = func_args.get("color", "")
                                await broadcast({
                                    "type":  "broadcast",
                                    "text":  f"訪客 #{session_id} 把螢幕顏色改成了 {color}",
                                    "color": color,
                                }, exclude=ws)

                            # 加入對話歷史
                            history.append({"role": "model", "content": output1})
                            history.append({
                                "role":    "tool",
                                "content": json.dumps(
                                    [{"output": result_msg}], ensure_ascii=False
                                )
                            })

                            # ── 略過第二次推理，直接將系統結果回傳給用戶 ────────
                            final_text = result_msg
                            log.info(f"直接回覆用戶: {final_text}")
                            
                            # 逐字送出（模擬 AI 打字效果）
                            for char in final_text:
                                await send({"type": "token", "text": char})
                                await asyncio.sleep(0.02) # 加微小延遲讓 UI 動畫更自然
                            await send({"type": "done"})
                            
                            # 歷史紀錄管理：記錄模型做了 Function Call，並記錄最終回覆
                            history.append({"role": "model", "content": output1})
                            history.append({"role": "assistant", "content": final_text})

                        else:
                            log.warning("Function call 解析失敗，直接回傳")
                            for char in output1:
                                await send({"type": "token", "text": char})
                            await send({"type": "done"})
                            history.append({"role": "model", "content": output1})

                    else:
                        # ── 普通回應，逐字送出 ───────────────
                        for char in output1:
                            await send({"type": "token", "text": char})
                        await send({"type": "done"})
                        history.append({"role": "model", "content": output1})

                    # ── 歷史管理（在 lock 內完成）───────────
                    if len(history) > 20:
                        history = history[-20:]
                    # lock 在此釋放，下一個排隊的訪客可以開始

            finally:
                # 訪客在「等待期間」關掉瀏覽器（CancelledError）時，
                # queue_size -= 1 在 lock 內還沒執行，需在此補正，
                # 否則計數器永久膨脹，後續訪客會看到假的排隊人數。
                if is_waiting:
                    queue_size = max(0, queue_size - 1)
                    log.info(f"連線 #{session_id} 在等待中斷線，已修正佇列計數（{queue_size}）")

    except WebSocketDisconnect:
        active_connections = max(0, active_connections - 1)
        all_sockets.discard(ws)
        log.info(f"連線 #{session_id} 中斷（目前 {active_connections} 人）")
    except Exception as e:
        log.error(f"WS 錯誤: {e}", exc_info=True)
        try:
            await send({"type": "error", "text": "發生錯誤，請重試"})
        except Exception:
            pass

# ─── Entry ────────────────────────────────────────────────
if __name__ == "__main__":
    url = get_url()
    print("\n" + "═"*56)
    print(f"  RPI5 AI 展示伺服器 (Gemma Function Calling)")
    print(f"  訪客連線  : {url}")
    print(f"  展示螢幕  : {url}/display  ← 在螢幕瀏覽器開啟這個")
    print(f"  QR Code  : {url}/qr.png")
    print(f"  WiFi     : {HOTSPOT_SSID} / {HOTSPOT_PASS}")
    print(f"  模型路徑  : {MODELS_DIR}")
    print("═"*56 + "\n")
    uvicorn.run("server:app", host="0.0.0.0", port=PORT,
                log_level="warning", reload=False)
