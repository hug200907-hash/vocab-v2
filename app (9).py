import json
import random
import time
import os
import re
import urllib.parse
import urllib.request
import requests
from datetime import datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components
from streamlit_local_storage import LocalStorage
from openai import OpenAI

# ============================================================
# 1. CẤU HÌNH APP & LOCAL STORAGE
# ============================================================

st.set_page_config(
    page_title="MochiVocab AI",
    page_icon="🍌",
    layout="centered"
)

local_storage = LocalStorage()

# ============================================================
# 2. HỆ THỐNG CẤP + MÓC
# ============================================================

LEVEL_HOOKS = {
    1: [1, 4, 12, 24],
    2: [25, 28, 36, 48],
    3: [49, 52, 60, 72],
    4: [73, 76, 84, 96],
    5: [97, 100, 108, 120],
}

MAX_LEVEL = 5
HOOKS_PER_LEVEL = 4

# ============================================================
# 3. SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "deck": [],
    "data_loaded": False,
    "review_item": None,
    "q_type": None,
    "q_data": {},
    "review_start_time": 0.0,
    "active_tab": "⏰ Ôn Tập",
    "temp_word": None,
    "review_started": False,
    "search_filter": "",
    "all_scanned_words": [],
    "current_batch_index": 0,
    "device_key": "",
    "cloud_bin_id": "",
    "p2p_room_id": "mochi-sync",
    "p2p_incoming_data": None
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        if isinstance(value, list):
            st.session_state[key] = []
        elif isinstance(value, dict):
            st.session_state[key] = {}
        else:
            st.session_state[key] = value

# ============================================================
# 4. FORMAT & HELPER
# ============================================================

def format_hours(hours):
    hours = float(hours)
    return f"{int(hours)} giờ" if hours.is_integer() else f"{hours:.1f} giờ"

def format_remaining(seconds):
    seconds = int(max(0, seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days} ngày {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def get_word_hint(word):
    word = word.strip()
    if len(word) <= 2:
        return f"{word[0]}..." if len(word) > 0 else ""
    hint_pattern = f"{word[0]} " + " ".join(["_"] * (len(word) - 2)) + f" {word[-1]}"
    return f"{hint_pattern} ({len(word)} ký tự)"

# ============================================================
# 5. THÔNG TIN CẤP / MÓC
# ============================================================

def get_level_name(level):
    names = {
        0: "🆕 Cấp 0 — Từ mới",
        1: "🥉 Cấp 1 — Đang hình thành",
        2: "🥈 Cấp 2 — Đã nhớ",
        3: "🥇 Cấp 3 — Nhớ khá tốt",
        4: "💎 Cấp 4 — Nhớ lâu",
        5: "🏆 Cấp 5 — Ghi nhớ rất tốt",
    }
    return names.get(level, "🆕 Cấp 0 — Từ mới")

def get_level_hooks(level):
    return LEVEL_HOOKS.get(level, [])

def get_hook_hours(item):
    level = int(item.get("level", 0))
    hook = int(item.get("hook", 0))

    if level <= 0: return 0
    hooks = get_level_hooks(level)
    if not hooks: return 0

    hook = max(1, min(hook, len(hooks)))
    return hooks[hook - 1]

def get_current_interval(item):
    return get_hook_hours(item)

# ============================================================
# 6. CHUẨN HÓA ITEM & LOAD/SAVE
# ============================================================

def normalize_item(item):
    item = dict(item)

    try: item["id"] = int(item.get("id", 0))
    except Exception: item["id"] = 0

    item["word"] = str(item.get("word", "")).strip()
    item["phonetic"] = str(item.get("phonetic", "")).strip()
    item["meaning"] = str(item.get("meaning", "")).strip()
    item["example"] = str(item.get("example", "")).strip()

    try: level = int(item.get("level", 0))
    except Exception: level = 0
    level = max(0, min(MAX_LEVEL, level))

    has_hook = "hook" in item
    try: hook = int(item.get("hook", 0))
    except Exception: hook = 0

    if not has_hook and level > 0:
        try: old_interval = float(item.get("interval", 1))
        except Exception: old_interval = 1

        best_level, best_hook, best_distance = 1, 1, float("inf")
        for lv, hooks in LEVEL_HOOKS.items():
            for hk, hours in enumerate(hooks, start=1):
                distance = abs(hours - old_interval)
                if distance < best_distance:
                    best_distance, best_level, best_hook = distance, lv, hk
        level, hook = best_level, best_hook

    item["level"] = level
    item["hook"] = 0 if level == 0 else max(1, min(HOOKS_PER_LEVEL, hook))

    for field in ["review_count", "correct_count", "wrong_count"]:
        try: item[field] = int(item.get(field, 0))
        except Exception: item[field] = 0

    item["last_response_time"] = item.get("last_response_time", None)
    item["last_result"] = item.get("last_result", None)

    next_review = item.get("next_review")
    if isinstance(next_review, datetime): item["next_review"] = next_review
    elif isinstance(next_review, str):
        try: item["next_review"] = datetime.fromisoformat(next_review)
        except Exception: item["next_review"] = datetime.now()
    else: item["next_review"] = datetime.now()

    item["interval"] = get_current_interval(item)
    return item

if not st.session_state.data_loaded:
    try:
        saved_data = local_storage.getItem("mochi_deck_data")
        if saved_data:
            items = json.loads(saved_data)
            if isinstance(items, list):
                st.session_state.deck = [normalize_item(x) for x in items if isinstance(x, dict)]
    except Exception: st.session_state.deck = []
    st.session_state.data_loaded = True

def save_deck():
    serializable_deck = []
    for item in st.session_state.deck:
        copy_item = dict(item)
        if isinstance(copy_item.get("next_review"), datetime):
            copy_item["next_review"] = copy_item["next_review"].isoformat()
        serializable_deck.append(copy_item)
    try:
        local_storage.setItem("mochi_deck_data", json.dumps(serializable_deck, ensure_ascii=False))
    except Exception: pass

def get_next_id():
    if not st.session_state.deck: return 1
    ids = [int(item.get("id", 0)) for item in st.session_state.deck if str(item.get("id", 0)).isdigit()]
    return max(ids) + 1 if ids else 1

# ============================================================
# 7. TÍCH HỢP LLM API
# ============================================================

def call_llm_api(prompt, api_key=None):
    active_key = api_key or st.secrets.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    if not active_key:
        st.error("❌ Chưa tìm thấy OPENROUTER_API_KEY.")
        return None

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=active_key.strip(),
        )
        response = client.chat.completions.create(
            model="minimax/minimax-m3:free",
            messages=[{"role": "user", "content": prompt}],
        )
        if response.choices and response.choices[0].message.content:
            content = response.choices[0].message.content
            cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
            return cleaned.strip()
        return None
    except Exception as e:
        st.error(f"❌ Lỗi OpenRouter API: {str(e)}")
        return None

# ============================================================
# 8. TRA TỪ & AUDIO
# ============================================================

def play_audio_script(word):
    safe_word = word.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", " ")
    js_code = f"""
    <script>
    window.speechSynthesis.cancel();
    var msg = new SpeechSynthesisUtterance('{safe_word}');
    msg.lang = 'en-US';
    msg.rate = 0.9;
    window.speechSynthesis.speak(msg);
    </script>
    """
    data_uri = "data:text/html;charset=utf-8," + urllib.parse.quote(js_code)
    components.iframe(src=data_uri, height=0)

# ============================================================
# 9. HEADER & ĐỒNG BỘ P2P REALTIME NÂNG CẤP
# ============================================================

st.title("🍌 MochiVocab")
st.caption("Dynamic Golden Time • Học theo cấp và 4 móc ghi nhớ")

with st.expander("⚡ Đồng bộ P2P Realtime - Gửi toàn bộ dữ liệu (WebRTC + PeerJS)"):
    st.caption("Gửi trực tiếp toàn bộ dữ liệu Sổ Tay từ máy này sang máy khác không qua Server trung gian.")

    col_room1, col_room2 = st.columns([3, 1])
    with col_room1:
        user_input = st.text_input("🔑 Nhập mã ghép nối P2P:", value=st.session_state.p2p_room_id, key="p2p_input_field")
        if user_input != st.session_state.p2p_room_id:
            st.session_state.p2p_room_id = user_input

    with col_room2:
        st.write("")
        st.write("")
        if st.button("🎲 Mã mới", key="p2p_rand_btn"):
            st.session_state.p2p_room_id = f"room-{random.randint(100, 999)}"
            st.rerun()

    sync_key = st.session_state.p2p_room_id.strip()

    # Chuẩn hóa dữ liệu toàn bộ deck để chuẩn bị truyền đi
    export_deck = []
    for item in st.session_state.deck:
        c_item = dict(item)
        if isinstance(c_item.get("next_review"), datetime):
            c_item["next_review"] = c_item["next_review"].isoformat()
        export_deck.append(c_item)

    json_deck_payload = json.dumps(export_deck, ensure_ascii=False)

    if sync_key:
        html_p2p_code = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="https://unpkg.com/peerjs@1.5.2/dist/peerjs.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ padding: 10px; background: transparent; }}
        .status-bar {{ 
            padding: 10px 15px; border-radius: 6px; font-weight: 600; font-size: 14px; 
            margin-bottom: 12px; background: #fff3cd; color: #856404; border: 1px solid #ffeeba;
        }}
        .btn-sync {{
            background-color: #ff4b4b; color: white; border: none; padding: 10px 18px; 
            font-size: 14px; font-weight: bold; border-radius: 6px; cursor: pointer; transition: 0.2s;
        }}
        .btn-sync:hover {{ background-color: #d43f3f; }}
        textarea {{ width: 100%; height: 90px; padding: 8px; font-size: 12px; border-radius: 6px; border: 1px solid #ccc; font-family: monospace; }}
    </style>
</head>
<body>

    <div id="status" class="status-bar">⏳ Đang kết nối PeerJS...</div>
    
    <div style="margin-bottom: 10px;">
        <button id="sendBtn" class="btn-sync">🚀 Gửi Toàn Bộ {len(export_deck)} Từ Sang Thiết Bị Khác</button>
    </div>

    <div>
        <label style="font-size:12px; font-weight:bold; color:#555;">Dữ liệu JSON nhận được từ máy khác (Tự động cập nhật):</label>
        <textarea id="remoteData" readonly placeholder="Chờ dữ liệu từ máy khác..."></textarea>
    </div>

    <script>
        const ROOM_ID = "st_p2p_full_{sync_key}";
        const LOCAL_DECK = {json_deck_payload};
        const statusEl = document.getElementById('status');
        const sendBtn = document.getElementById('sendBtn');
        const remoteData = document.getElementById('remoteData');
        
        let peer = null;
        let conn = null;

        function updateStatus(text, type = 'warning') {{
            statusEl.innerText = text;
            if (type === 'success') {{
                statusEl.style.background = '#d4edda'; statusEl.style.color = '#155724'; statusEl.style.borderColor = '#c3e6cb';
            }} else if (type === 'error') {{
                statusEl.style.background = '#f8d7da'; statusEl.style.color = '#721c24'; statusEl.style.borderColor = '#f5c6cb';
            }} else {{
                statusEl.style.background = '#fff3cd'; statusEl.style.color = '#856404'; statusEl.style.borderColor = '#ffeeba';
            }}
        }}

        function initPeer() {{
            if (typeof Peer === 'undefined') {{ setTimeout(initPeer, 300); return; }}

            peer = new Peer(ROOM_ID, {{
                host: '0.peerjs.com', port: 443, path: '/', secure: true,
                config: {{ iceServers: [ {{ urls: 'stun:stun.l.google.com:19302' }} ] }}
            }});

            peer.on('open', (id) => {{
                updateStatus("🟢 Đã sẵn sàng! Hãy mở máy thứ 2 và nhập cùng mã '{sync_key}'.", "warning");
            }});

            peer.on('connection', (c) => {{
                conn = c;
                setupEvents();
            }});

            peer.on('error', (err) => {{
                if (err.type === 'unavailable-id') {{
                    updateStatus("🔄 Đã tìm thấy máy chủ phòng. Đang ghép nối P2P...", "warning");
                    if (peer) peer.destroy();
                    
                    peer = new Peer({{
                        host: '0.peerjs.com', port: 443, path: '/', secure: true,
                        config: {{ iceServers: [ {{ urls: 'stun:stun.l.google.com:19302' }} ] }}
                    }});
                    peer.on('open', () => {{
                        conn = peer.connect(ROOM_ID, {{ reliable: true }});
                        setupEvents();
                    }});
                }} else {{
                    updateStatus("❌ Lỗi P2P: " + err.type, 'error');
                }}
            }});
        }}

        function setupEvents() {{
            if (!conn) return;

            conn.on('open', () => {{
                updateStatus("✅ ĐÃ KẾT NỐI P2P REALTIME! Bạn có thể bấm gửi dữ liệu.", 'success');
            }});

            conn.on('data', (data) => {{
                remoteData.value = typeof data === 'object' ? JSON.stringify(data) : data;
                updateStatus("🎉 ĐÃ NHẬN TOÀN BỘ DỮ LIỆU TỪ MÁY BÊN KÌA!", 'success');
            }});

            conn.on('close', () => {{
                updateStatus("⚠️ Kết nối P2P đã ngắt.", 'error');
            }});
        }}

        sendBtn.addEventListener('click', () => {{
            if (conn && conn.open) {{
                conn.send(LOCAL_DECK);
                updateStatus("🚀 Đã gửi toàn bộ " + LOCAL_DECK.length + " từ vựng sang máy đối phương!", 'success');
            }} else {{
                alert("Chưa kết nối tới thiết bị khác! Hãy đảm bảo 2 máy đã nhập chung mã.");
            }}
        }});

        setTimeout(initPeer, 200);
    </script>
</body>
</html>
"""
        p2p_data_uri = "data:text/html;charset=utf-8," + urllib.parse.quote(html_p2p_code)
        components.iframe(src=p2p_data_uri, height=220)

    # Khung nhập JSON nhận được từ P2P nếu muốn lưu trực tiếp vào Sổ tay
    raw_p2p_json = st.text_area("📥 Hoặc dán chuỗi JSON nhận được từ máy khác vào đây để nạp vào Sổ tay:", height=80)
    if st.button("📥 Nạp Dữ Liệu Này Vào Sổ Tay"):
        if raw_p2p_json.strip():
            try:
                parsed_items = json.loads(raw_p2p_json)
                if isinstance(parsed_items, list):
                    st.session_state.deck = [normalize_item(x) for x in parsed_items if isinstance(x, dict)]
                    save_deck()
                    st.success(f"🎉 Đã nạp thành công {len(st.session_state.deck)} từ vựng vào Sổ tay!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ Định dạng JSON không hợp lệ.")
            except Exception as e:
                st.error(f"❌ Lỗi xử lý dữ liệu: {str(e)}")

# ============================================================
# 10. CHUYỂN TAB & NỘI DUNG CHÍNH (Đơn giản hóa cho ngắn gọn)
# ============================================================

now = datetime.now()
due_count = sum(1 for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= now)

selected_tab = st.radio(
    "Navigation",
    options=["⏰ Ôn Tập", "🔍 Tra Từ Mới", "📋 Sổ Tay"],
    format_func=lambda x: f"⏰ Ôn Tập ({due_count})" if "Ôn Tập" in x else (f"📋 Sổ Tay ({len(st.session_state.deck)})" if "Sổ Tay" in x else x),
    horizontal=True,
    label_visibility="collapsed"
)

st.markdown("---")

if selected_tab == "📋 Sổ Tay":
    st.subheader("📋 Sổ Tay Từ Vựng")
    st.metric("Tổng số từ", len(st.session_state.deck))
    
    table_data = [{"ID": x.get("id"), "Từ": x.get("word", "").upper(), "Nghĩa": x.get("meaning", ""), "Cấp": f"Cấp {x.get('level', 0)}"} for x in st.session_state.deck]
    st.dataframe(table_data, use_container_width=True)

    if st.button("🗑️ Xóa toàn bộ từ vựng"):
        st.session_state.deck = []
        save_deck()
        st.rerun()

elif selected_tab == "🔍 Tra Từ Mới":
    st.subheader("🔍 Thêm Từ Mới")
    w = st.text_input("Từ tiếng Anh:")
    m = st.text_input("Nghĩa tiếng Việt:")
    if st.button("Thêm Từ"):
        if w and m:
            new_item = {
                "id": get_next_id(), "word": w, "meaning": m, "phonetic": "", "example": "",
                "level": 0, "hook": 0, "interval": 0, "review_count": 0, "correct_count": 0,
                "wrong_count": 0, "next_review": datetime.now()
            }
            st.session_state.deck.append(new_item)
            save_deck()
            st.success(f"Đã thêm {w}")
            st.rerun()

else:
    st.subheader("⏰ Ôn Tập")
    st.info(f"Hiện có {due_count} từ cần ôn tập.")
