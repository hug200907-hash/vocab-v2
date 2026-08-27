import json
import random
import time
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import streamlit as st
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

# API công cộng miễn phí dùng làm CSDL Cloud cho Sync Key (JSONBin Public hoặc tương đương)
# Sử dụng KV storage miễn phí qua jsonbin.io / myjson
PUBLIC_SYNC_API = "https://api.jsonbin.io/v3/b"

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
# 6. CHUẨN HÓA ITEM & LOAD/SAVE (LOCAL + CLOUD SYNC)
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

# --- CLOUD SYNC HELPERS ---
def sync_push_to_cloud(key, deck_data):
    """Đẩy dữ liệu lên Cloud theo Key 8 số"""
    if not key or len(key) != 8: return False
    try:
        url = f"https://api.keyvalue.xyz/set/{key}"
        data_str = json.dumps(deck_data, ensure_ascii=False)
        req = urllib.request.Request(url, data=data_str.encode('utf-8'), headers={'Content-Type': 'text/plain'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False

def sync_pull_from_cloud(key):
    """Tải dữ liệu từ Cloud theo Key 8 số"""
    if not key or len(key) != 8: return None
    try:
        url = f"https://api.keyvalue.xyz/get/{key}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode('utf-8')
            if data:
                return json.loads(data)
    except Exception:
        return None
    return None

if not st.session_state.data_loaded:
    try:
        saved_key = local_storage.getItem("mochi_device_key")
        if saved_key: st.session_state.device_key = str(saved_key)
        
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
        if st.session_state.device_key:
            sync_push_to_cloud(st.session_state.device_key, serializable_deck)
    except Exception: pass

def get_next_id():
    if not st.session_state.deck: return 1
    ids = [int(item.get("id", 0)) for item in st.session_state.deck if str(item.get("id", 0)).isdigit()]
    return max(ids) + 1 if ids else 1

# ============================================================
# 7. TÍCH HỢP LLM API (DÀNH CHO TRA TỪ, QUÉT BÀI & RESET)
# ============================================================

def call_llm_api(prompt, api_key=None):
    active_key = api_key or st.secrets.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    if not active_key:
        st.error(
            "❌ **Chưa tìm thấy OPENROUTER_API_KEY.**\n\n"
            "Vào Streamlit Cloud → Settings → Secrets và thêm:\n"
            '```toml\nOPENROUTER_API_KEY = "sk-or-v1-..."\n```'
        )
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
# 8. TRA TỪ & DICTIONARY API
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_dictionary_data(word):
    if not word: return None
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list) and data: return data
    except Exception: pass
    return None

def fetch_online_example(word):
    data = fetch_dictionary_data(word)
    if not data: return None
    for meaning_obj in data[0].get("meanings", []):
        for def_obj in meaning_obj.get("definitions", []):
            example = def_obj.get("example")
            if example: return example
    return None

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
    st.components.v1.html(js_code, height=0)

# ============================================================
# 9. TẠO CÂU HỎI TĨNH CHO TAB ÔN TẬP
# ============================================================

FALLBACK_MEANINGS_POOL = [
    "Sự phát triển", "Khả năng thích nghi", "Thành tựu", "Môi trường", "Kinh nghiệm",
    "Khả năng phục hồi", "Đổi mới sáng tạo", "Thách thức", "Cơ hội", "Mục tiêu trọng tâm",
    "Tiềm năng lớn", "Giải pháp hiệu quả", "Tác động tích cực", "Sự kiên trì", "Nhận thức"
]

FALLBACK_WORDS_POOL = [
    "resilience", "innovate", "experience", "development", "adaptation",
    "achievement", "environment", "challenge", "opportunity", "strategy",
    "perspective", "efficiency", "persistance", "solution", "capability"
]

def generate_distractors(target, source_list, fallback_pool, count=3):
    options = [target]
    filtered_source = [x for x in source_list if x and x.lower() != target.lower()]
    random.shuffle(filtered_source)

    for item in filtered_source:
        if len(options) >= count + 1: break
        if item not in options: options.append(item)

    if len(options) < count + 1:
        shuffled_fallback = random.sample(fallback_pool, len(fallback_pool))
        for fb in shuffled_fallback:
            if len(options) >= count + 1: break
            if fb.lower() not in [x.lower() for x in options]: options.append(fb)

    random.shuffle(options)
    return options

def prepare_review_question(item):
    q_types = [
        "CHOICE_MEANING",
        "FILL_BLANK",
        "SPELLING",
        "CONTEXT_MATCH",
        "FLASHCARD_TRUE_FALSE",
        "MEANING_CHOICE",
    ]
    chosen_q = random.choice(q_types)

    st.session_state.review_item = item
    st.session_state.q_type = chosen_q
    st.session_state.review_start_time = time.time()
    st.session_state.q_data = {}

    word = item.get("word", "").strip()
    meaning = item.get("meaning", "").strip()
    example = item.get("example", "").strip()

    if not example:
        online_example = fetch_online_example(word)
        example = online_example if online_example else f"It is important to understand {word}."

    deck_words = [x.get("word", "").strip() for x in st.session_state.deck]
    deck_meanings = [x.get("meaning", "").strip() for x in st.session_state.deck]

    if chosen_q == "CHOICE_MEANING":
        options = generate_distractors(meaning, deck_meanings, FALLBACK_MEANINGS_POOL)
        st.session_state.q_data = {"question": word, "options": options, "answer": meaning}

    elif chosen_q == "FILL_BLANK":
        blank_sentence = re.sub(r"\b" + re.escape(word) + r"\b", "_____", example, flags=re.IGNORECASE)
        if blank_sentence == example:
            blank_sentence = f"{example} _____"
        st.session_state.q_data = {"sentence": blank_sentence, "answer": word, "word": word}

    elif chosen_q == "SPELLING":
        st.session_state.q_data = {"question": meaning, "answer": word}

    elif chosen_q == "CONTEXT_MATCH":
        options = generate_distractors(meaning, deck_meanings, FALLBACK_MEANINGS_POOL)
        st.session_state.q_data = {"context": example, "word": word, "options": options, "answer": meaning}

    elif chosen_q == "FLASHCARD_TRUE_FALSE":
        is_true = random.choice([True, False])
        if is_true or not deck_meanings:
            disp_meaning = meaning
            ans = True
        else:
            other_meanings = [m for m in deck_meanings if m.lower() != meaning.lower()]
            disp_meaning = random.choice(other_meanings) if other_meanings else random.choice(FALLBACK_MEANINGS_POOL)
            ans = False
        st.session_state.q_data = {"word": word, "disp_meaning": disp_meaning, "is_true": ans, "answer": ans}

    elif chosen_q == "MEANING_CHOICE":
        options = generate_distractors(word, deck_words, FALLBACK_WORDS_POOL)
        st.session_state.q_data = {"word": word, "question": meaning, "options": options, "answer": word}

# ============================================================
# 10. TIẾN / LÙI MÓC & XỬ LÝ ĐÁP ÁN
# ============================================================

def advance_after_correct(item):
    level = int(item.get("level", 0))
    hook = int(item.get("hook", 0))

    if level == 0: level, hook = 1, 1
    elif level < MAX_LEVEL:
        if hook < 4: hook += 1
        else: level += 1; hook = 1
    else:
        hook = min(4, hook + 1) if hook < 4 else 4
        level = 5

    item["level"] = level
    item["hook"] = hook
    item["interval"] = get_current_interval(item)

def move_back_after_wrong(item):
    level = int(item.get("level", 0))
    hook = int(item.get("hook", 0))

    if level == 0: level, hook = 0, 0
    elif level == 1: hook = max(1, hook - 1)
    else:
        if hook > 1: hook -= 1
        else: level -= 1; hook = 4

    item["level"] = level
    item["hook"] = hook
    item["interval"] = get_current_interval(item)

def process_answer(is_correct, correct_ans_text):
    item = st.session_state.review_item
    if item is None: return

    response_time = max(0.1, time.time() - st.session_state.review_start_time)
    old_level = int(item.get("level", 0))
    old_hook = int(item.get("hook", 0))

    now = datetime.now()
    next_rev = item.get("next_review", now)

    overdue_hours = (now - next_rev).total_seconds() / 3600.0 if now > next_rev else 0
    item["review_count"] = int(item.get("review_count", 0)) + 1

    if is_correct:
        item["correct_count"] = int(item.get("correct_count", 0)) + 1
        item["last_result"] = "correct"
        advance_after_correct(item)
    else:
        item["wrong_count"] = int(item.get("wrong_count", 0)) + 1
        item["last_result"] = "wrong"
        move_back_after_wrong(item)
        if overdue_hours > 24 and item["level"] > 0:
            move_back_after_wrong(item)

    item["last_response_time"] = round(response_time, 2)
    new_interval_hours = get_current_interval(item)

    if new_interval_hours <= 0: item["next_review"] = datetime.now()
    else: item["next_review"] = datetime.now() + timedelta(hours=new_interval_hours)

    item["interval"] = new_interval_hours

    if is_correct:
        st.success("✨ Chính xác!")
        st.write(f"⚡ Thời gian phản hồi: **{response_time:.1f} giây**")
        st.success(f"📈 Cấp {old_level}, móc {old_hook}/4 → Cấp {item['level']}, móc {item['hook']}/4")
        if new_interval_hours > 0: st.info(f"⏰ Móc tiếp theo: **{format_hours(new_interval_hours)}**")
        if old_level < item["level"]:
            st.balloons()
            st.success(f"🎉 Đã lên Cấp {item['level']}!")
        if item["level"] == 5 and item["hook"] == 4: st.success("🏆 Từ này đã đạt Cấp 5 — Móc 4!")
    else:
        st.error("❌ Chưa chính xác.")
        st.warning(f"Đáp án đúng: **{correct_ans_text}**")
        st.warning(f"📉 Cấp {old_level}, móc {old_hook}/4 → Cấp {item['level']}, móc {item['hook']}/4")
        if new_interval_hours > 0: st.info(f"🔄 Móc mới: **{format_hours(new_interval_hours)}**")

    save_deck()

    st.session_state.review_item = None
    st.session_state.q_type = None
    st.session_state.q_data = {}
    st.session_state.review_start_time = 0

    time.sleep(0.8)
    st.rerun()

def reset_all_to_level_zero():
    for item in st.session_state.deck:
        item["level"] = 0
        item["hook"] = 0
        item["interval"] = 0
        item["next_review"] = datetime.now()
        item["review_count"] = 0
        item["correct_count"] = 0
        item["wrong_count"] = 0
        item["last_response_time"] = None
        item["last_result"] = None

    st.session_state.review_item = None
    st.session_state.q_type = None
    st.session_state.q_data = {}
    st.session_state.review_started = False
    save_deck()

# ============================================================
# 11. HEADER & DỒNG BỘ THIẾT BỊ (KEY 8 SỐ)
# ============================================================

st.title("🍌 MochiVocab")
st.caption("Dynamic Golden Time • Học theo cấp và 4 móc ghi nhớ")

# --- EXPANDER ĐĂNG NHẬP / CHIA SẺ KEY 8 SỐ ---
with st.expander("📲 Đăng Nhập / Đồng Bộ NhIều Thiết Bị (Key 8 Số)"):
    curr_key = st.session_state.get("device_key", "")
    if curr_key:
        st.success(f"🔑 Mã kết nối thiết bị của bạn: **{curr_key}**")
        st.caption("Dùng 8 số này nhập vào máy khác để sài chung dữ liệu.")
    else:
        st.info("💡 Chưa kết nối Key đồng bộ. Bạn có thể Tạo Key mới hoặc nhập Key từ máy khác.")

    c_k1, c_k2 = st.columns(2)
    with c_k1:
        if st.button("➕ Tạo Key 8 Số Mới (Máy này làm gốc)"):
            new_key = f"{random.randint(10000000, 99999999)}"
            st.session_state.device_key = new_key
            local_storage.setItem("mochi_device_key", new_key)
            save_deck()
            st.success(f"🎉 Đã tạo Key: **{new_key}**. Lưu giữ mã này để nhập ở máy khác!")
            time.sleep(1)
            st.rerun()

    with c_k2:
        input_key = st.text_input("Nhập Key 8 số từ máy khác:", max_chars=8, placeholder="8 chữ số...").strip()
        if st.button("🔗 Đăng Nhập & Đồng Bộ"):
            if len(input_key) == 8 and input_key.isdigit():
                with st.spinner("🔄 Đang tải dữ liệu từ Cloud..."):
                    cloud_data = sync_pull_from_cloud(input_key)
                    if cloud_data is not None:
                        st.session_state.device_key = input_key
                        local_storage.setItem("mochi_device_key", input_key)
                        st.session_state.deck = [normalize_item(x) for x in cloud_data if isinstance(x, dict)]
                        save_deck()
                        st.success("✅ Kết nối thành công! Đã tải dữ liệu về máy.")
                        time.sleep(0.8)
                        st.rerun()
                    else:
                        st.error("❌ Key này chưa có dữ liệu hoặc không tồn tại!")
            else:
                st.warning("⚠️ Vui lòng nhập đúng 8 chữ số.")

now = datetime.now()
due_count = sum(1 for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= now)

tab_options = ["⏰ Ôn Tập", "🔍 Tra Từ Mới", "📄 Quét Bài Đọc", "📋 Sổ Tay"]
tab_labels = {
    "⏰ Ôn Tập": f"⏰ Ôn Tập ({due_count})",
    "🔍 Tra Từ Mới": "🔍 Tra Từ Mới",
    "📄 Quét Bài Đọc": "📄 Quét Bài Đọc",
    "📋 Sổ Tay": f"📋 Sổ Tay ({len(st.session_state.deck)})",
}

selected_tab = st.radio(
    "Navigation",
    options=tab_options,
    format_func=lambda x: tab_labels[x],
    key="active_tab",
    horizontal=True,
    label_visibility="collapsed"
)

st.markdown("---")

# ============================================================
# 12. TAB ÔN TẬP
# ============================================================

if selected_tab == "⏰ Ôn Tập":
    st.subheader("⏰ Ôn tập đúng Thời Điểm Vàng")
    now = datetime.now()
    due_items = [x for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= now]

    if not st.session_state.deck:
        st.warning("📚 Sổ tay đang trống.")
        st.write("Hãy sang **🔍 Tra Từ Mới** hoặc **📄 Quét Bài Đọc** để thêm từ.")

    elif not due_items:
        st.session_state.review_started = False
        st.session_state.review_item = None
        st.session_state.q_type = None
        st.session_state.q_data = {}

        next_item = min(st.session_state.deck, key=lambda x: x["next_review"])
        remaining = (next_item["next_review"] - datetime.now()).total_seconds()

        st.success("🎉 Hiện tại không có từ nào đến Thời Điểm Vàng.")

        col1, col2 = st.columns(2)
        with col1: st.metric("Từ tiếp theo", next_item["word"].upper())
        with col2: st.metric("Cấp", next_item["level"])

        st.info(f"⏰ Còn khoảng **{format_remaining(remaining)}**")
        remaining_seconds = max(0, int(remaining))

        st.components.v1.html(
            f"""
            <div style="text-align:center; background:#262730; color:#00FF66; padding:20px; border-radius:15px; margin-top:15px;">
                <div style="font-size:13px; color:#AAAAAA; margin-bottom:8px;">THỜI ĐIỂM VÀNG TIẾP THEO</div>
                <div id="mochi-countdown" style="font-size:30px; font-weight:bold; font-family:monospace;">--:--:--</div>
            </div>
            <script>
                let remaining = {remaining_seconds};
                function updateCountdown() {{
                    const countdown = document.getElementById("mochi-countdown");
                    if (!countdown) return;
                    if (remaining <= 0) {{ countdown.innerText = "🔥 ĐÃ ĐẾN GIỜ! Hãy chọn lại tab Ôn Tập"; return; }}
                    const days = Math.floor(remaining / 86400);
                    const hours = Math.floor((remaining % 86400) / 3600);
                    const minutes = Math.floor((remaining % 3600) / 60);
                    const seconds = remaining % 60;
                    let result = days > 0 ? `${{days}} ngày ${{String(hours).padStart(2, "0")}}:${{String(minutes).padStart(2, "0")}}:${{String(seconds).padStart(2, "0")}}`
                                          : `${{String(hours).padStart(2, "0")}}:${{String(minutes).padStart(2, "0")}}:${{String(seconds).padStart(2, "0")}}`;
                    countdown.innerText = result;
                    remaining--;
                }}
                updateCountdown();
                setInterval(updateCountdown, 1000);
            </script>
            """,
            height=120
        )

    else:
        if not st.session_state.review_started:
            st.success(f"🔥 Có **{len(due_items)} từ** đang đến Thời Điểm Vàng.")
            st.markdown("---")
            st.markdown("### 🧠 Sẵn sàng ôn tập?\nMochiVocab sẽ chọn một từ đang đến giờ và bắt đầu tính thời gian phản hồi.")

            if st.button("▶️ BẮT ĐẦU ÔN TẬP", type="primary", use_container_width=True, key="start_review"):
                min_level = min(x.get("level", 0) for x in due_items)
                candidates = [x for x in due_items if x.get("level", 0) == min_level]
                item = random.choice(candidates)

                st.session_state.review_started = True
                prepare_review_question(item)
                st.rerun()

        else:
            current_item = st.session_state.review_item
            if current_item is None:
                min_level = min(x.get("level", 0) for x in due_items)
                candidates = [x for x in due_items if x.get("level", 0) == min_level]
                item = random.choice(candidates)

                prepare_review_question(item)
                st.rerun()

            item = st.session_state.review_item
            q_type = st.session_state.q_type
            q_data = st.session_state.q_data

            if st.button("⏹️ Dừng ôn tập", key="stop_review"):
                st.session_state.review_started = False
                st.session_state.review_item = None
                st.session_state.q_type = None
                st.session_state.q_data = {}
                st.session_state.review_start_time = 0
                st.rerun()

            level = int(item.get("level", 0))
            hook = int(item.get("hook", 0))
            progress = hook / 4 if level > 0 else 0

            st.progress(progress)

            col1, col2 = st.columns(2)
            with col1: st.caption(get_level_name(level))
            with col2: st.caption(f"Móc: {hook}/4" if level > 0 else "Móc: 0/4")

            if level == 0: st.caption("⏰ Khoảng ôn: **0 giờ — Từ mới**")
            else:
                current_hours = get_current_interval(item)
                st.caption(f"📐 Móc hiện tại: **{format_hours(current_hours)}**")

            st.markdown("---")

            # 1. CHOICE MEANING
            if q_type == "CHOICE_MEANING":
                st.markdown("### 🎲 TRẮC NGHIỆM CHỌN NGHĨA")
                st.info(f"Từ: **{item['word'].upper()}** `{item.get('phonetic', '')}`")

                if st.button("🔊 Nghe", key="choice_audio"): play_audio_script(item["word"])

                st.write("Chọn nghĩa tiếng Việt:")
                for index, option in enumerate(q_data.get("options", [])):
                    if st.button(option, key=f"choice_{item['id']}_{index}"):
                        process_answer(option.strip().lower() == item["meaning"].strip().lower(), item["meaning"])

            # 2. FILL BLANK
            elif q_type == "FILL_BLANK":
                st.markdown("### ✏️ ĐIỀN TỪ VÀO CHỖ TRỐNG")
                st.info(f"**{q_data.get('sentence', '')}**")
                
                hint = get_word_hint(item['word'])
                st.caption(f"💡 Gợi ý cấu trúc từ: `{hint}`")

                user_ans = st.text_input("Từ còn thiếu:", key=f"fill_{item['id']}")
                if st.button("Xác Nhận", type="primary", key=f"fill_submit_{item['id']}"):
                    process_answer(user_ans.strip().lower() == item["word"].strip().lower(), item["word"].upper())

            # 3. SPELLING
            elif q_type == "SPELLING":
                st.markdown("### ✍️ LUYỆN CHÍNH TẢ")
                st.info(f"Nghĩa tiếng Việt: **{item['meaning'].upper()}**")

                hint = get_word_hint(item['word'])
                st.caption(f"💡 Gợi ý cấu trúc từ: `{hint}`")

                user_ans = st.text_input("Gõ từ tiếng Anh:", key=f"spell_{item['id']}")
                if st.button("Xác Nhận", type="primary", key=f"spell_submit_{item['id']}"):
                    process_answer(user_ans.strip().lower() == item["word"].strip().lower(), item["word"].upper())

            # 4. CONTEXT MATCH
            elif q_type == "CONTEXT_MATCH":
                st.markdown("### 🧠 NGHĨA THEO NGỮ CẢNH")
                st.info(f'"{q_data.get("context", "")}"')
                st.write(f'Từ **{item["word"].upper()}** có nghĩa là gì?')

                for index, option in enumerate(q_data.get("options", [])):
                    if st.button(option, key=f"context_{item['id']}_{index}"):
                        process_answer(option.strip().lower() == item["meaning"].strip().lower(), item["meaning"])

            # 5. TRUE / FALSE
            elif q_type == "FLASHCARD_TRUE_FALSE":
                st.markdown("### ⚡ FLASHCARD PHẢN XẠ")
                st.info(f"Từ: **{item['word']}**\n\nNghĩa: **{q_data.get('disp_meaning', '')}**")
                st.write("Thông tin trên đúng hay sai?")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ ĐÚNG", type="primary", key=f"true_{item['id']}"):
                        process_answer(q_data["is_true"], "ĐÚNG" if q_data["is_true"] else "SAI")
                with col2:
                    if st.button("❌ SAI", key=f"false_{item['id']}"):
                        process_answer(not q_data["is_true"], "SAI" if not q_data["is_true"] else "ĐÚNG")

            # 6. MEANING CHOICE
            elif q_type == "MEANING_CHOICE":
                st.markdown("### 🔤 NGHĨA → CHỌN TỪ TIẾNG ANH")
                st.info(f"Nghĩa: **{q_data.get('question', '').upper()}**")
                st.write("Chọn từ tiếng Anh:")

                for index, option in enumerate(q_data.get("options", [])):
                    if st.button(option.upper(), key=f"mchoice_{item['id']}_{index}"):
                        process_answer(option.strip().lower() == item["word"].strip().lower(), item["word"].upper())

# ============================================================
# 13. TAB TRA TỪ MỚI
# ============================================================

elif selected_tab == "🔍 Tra Từ Mới":
    st.subheader("🔍 Tra cứu & Thêm từ mới (AI Trợ Lý)")

    word_input = st.text_input("Nhập từ tiếng Anh:", placeholder="Ví dụ: resilience, innovate...").strip().lower()

    if st.button("🔎 Tra Từ Với AI", type="primary"):
        if word_input:
            with st.spinner("🤖 AI đang phân tích nghĩa, phiên âm và ví dụ..."):
                prompt = f"""
Hãy đóng vai từ điển Anh-Việt cao cấp. Phân tích từ tiếng Anh: "{word_input}".
Trả về duy nhất định dạng JSON thô (không bọc trong markdown):
{{
  "phonetic": "/phiên âm IPA/",
  "meaning": "Nghĩa tiếng Việt chuẩn, ngắn gọn",
  "example": "1 câu ví dụ tiếng Anh ngắn, tự nhiên"
}}
"""
                res = call_llm_api(prompt)
                if res:
                    try:
                        ai_data = json.loads(res)
                        st.session_state.temp_word = {
                            "word": word_input,
                            "phonetic": ai_data.get("phonetic", ""),
                            "meaning": ai_data.get("meaning", ""),
                            "example": ai_data.get("example", f"It is important to understand {word_input}.")
                        }
                    except Exception:
                        st.error("❌ Lỗi cấu trúc JSON từ AI. Vui lòng bấm thử lại!")
                else:
                    st.error("❌ Không thể kết nối với AI API.")

    data = st.session_state.get("temp_word")
    if data and data.get("word") == word_input:
        st.markdown("---")
        st.info(f"**{data['word'].upper()}** `{data.get('phonetic', '')}`")

        manual_meaning = st.text_input("Chỉnh sửa nghĩa tiếng Việt:", value=data.get("meaning", ""), key=f"manual_m_{data['word']}")
        manual_example = st.text_area("Chỉnh sửa câu ví dụ:", value=data.get("example", ""), height=70, key=f"manual_e_{data['word']}")

        data["meaning"] = manual_meaning.strip()
        data["example"] = manual_example.strip()
        st.session_state.temp_word = data

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔊 Nghe", key="new_word_audio"): play_audio_script(data["word"])
        with col2:
            if st.button("➕ Thêm vào Sổ Tay", key="add_new_word", type="primary"):
                exists = any(x.get("word", "").strip().lower() == data["word"].strip().lower() for x in st.session_state.deck)
                if exists: st.warning("⚠️ Từ này đã có trong Sổ Tay.")
                elif not data.get("meaning", "").strip(): st.error("⚠️ Cần có nghĩa tiếng Việt trước khi lưu!")
                else:
                    new_item = {
                        "id": get_next_id(),
                        "word": data["word"],
                        "phonetic": data.get("phonetic", ""),
                        "meaning": data["meaning"],
                        "example": data["example"],
                        "level": 0, "hook": 0, "interval": 0,
                        "review_count": 0, "correct_count": 0, "wrong_count": 0,
                        "last_response_time": None, "last_result": None,
                        "next_review": datetime.now()
                    }
                    st.session_state.deck.append(new_item)
                    save_deck()
                    st.success(f"✅ Đã thêm **{data['word'].upper()}** vào Sổ Tay!")
                    time.sleep(0.5)
                    st.rerun()

# ============================================================
# 14. TAB QUÉT BÀI ĐỌC
# ============================================================

elif selected_tab == "📄 Quét Bài Đọc":
    st.subheader("📄 Quét Bài Đọc & Lọc Từ Vựng Theo Trình Độ (A1 - C2)")
    st.caption("AI sẽ phân tích bài đọc, lọc ra các từ vựng thuộc đúng cấp độ bạn chọn và giải nghĩa chuẩn theo ngữ cảnh.")

    input_text = st.text_area("Nhập bài đọc tiếng Anh:", placeholder="Dán đoạn văn tiếng Anh vào đây...", height=160)

    col_b1, col_b2 = st.columns([2, 1])
    with col_b1:
        target_band = st.selectbox(
            "🎯 Chọn trình độ từ vựng muốn lọc:",
            options=[
                "🟢 Level A1 - Nhập môn",
                "🟢 Level A2 - Sơ cấp",
                "🟡 Level B1 / IELTS 4.0 - 5.0",
                "🟡 Level B2 / IELTS 5.5 - 6.5",
                "🔴 Level C1 / IELTS 7.0 - 8.0",
                "🔴 Level C2 / IELTS 8.5 - 9.0"
            ],
            index=3
        )
    with col_b2:
        batch_size = st.selectbox("Số từ / Batch:", options=[10, 15, 20], index=0)

    if st.button("🚀 AI Phân Tích & Lọc Từ", type="primary", use_container_width=True):
        if not input_text.strip():
            st.warning("⚠️ Vui lòng dán bài đọc trước khi phân tích!")
        else:
            with st.spinner(f"🤖 AI đang đọc bài văn và lọc từ vựng trình độ {target_band}..."):
                existing_words = [item.get("word", "").strip().lower() for item in st.session_state.deck]

                prompt_band = f"""
Bạn là chuyên gia ngôn ngữ tiếng Anh. Dựa vào bài đọc sau:
---
{input_text[:2000]}
---

Lọc ra các từ vựng thuộc đúng trình độ: **{target_band}**.
Bỏ qua các từ ĐÃ CÓ trong danh sách bên dưới:
{json.dumps(existing_words[:100])}

Output duy nhất dạng JSON Array:
[
  {{
    "word": "từ tiếng anh",
    "phonetic": "/phiên âm/",
    "meaning": "nghĩa việt chuẩn ngữ cảnh bài đọc",
    "example": "câu ví dụ ngắn",
    "level_tag": "B2"
  }}
]
Chỉ trả về JSON thô.
"""
                res = call_llm_api(prompt_band)
                if res:
                    try:
                        scanned_data = json.loads(res)
                        st.session_state.all_scanned_words = scanned_data
                        st.session_state.current_batch_index = 0
                        st.success(f"✅ Tìm thấy {len(scanned_data)} từ thuộc trình độ {target_band}!")
                    except Exception:
                        st.error("❌ Lỗi cấu trúc JSON từ AI. Vui lòng thử lại!")
                else:
                    st.error("❌ Không thể kết nối tới AI API.")

    if st.session_state.get("all_scanned_words"):
        all_items = st.session_state.all_scanned_words
        total_items = len(all_items)
        total_batches = (total_items + batch_size - 1) // batch_size
        idx = st.session_state.current_batch_index

        st.markdown("---")
        st.info(f"📊 Tìm thấy **{total_items} từ**. Đang xem **Batch {idx + 1}/{total_batches}**")

        col_nav1, col_nav2 = st.columns(2)
        with col_nav1:
            if st.button("⬅️ Batch trước") and idx > 0:
                st.session_state.current_batch_index -= 1
                st.rerun()
        with col_nav2:
            if st.button("Batch tiếp ➡️") and idx < total_batches - 1:
                st.session_state.current_batch_index += 1
                st.rerun()

        start_i = idx * batch_size
        end_i = min((idx + 1) * batch_size, total_items)
        current_batch = all_items[start_i:end_i]

        st.markdown("### ✏️ Duyệt & Chỉnh Sửa Batch Này:")

        final_list = []
        for i, item in enumerate(current_batch):
            col_w, col_m = st.columns([2, 3])
            with col_w:
                st.markdown(f"**{item['word'].upper()}** `{item.get('phonetic', '')}`")
                st.caption(f"🏷️ Trình độ: **{item.get('level_tag', 'N/A')}**")
            with col_m:
                meaning_val = st.text_input(
                    f"Nghĩa ({item['word']}):",
                    value=item.get('meaning', ''),
                    key=f"cefr_m_{idx}_{i}"
                )
                item['meaning'] = meaning_val
            final_list.append(item)

        if st.button("💾 LƯU BATCH NÀY VÀO SỔ TAY", type="primary", use_container_width=True):
            saved_count = 0
            for item in final_list:
                if item["meaning"].strip():
                    new_item = {
                        "id": get_next_id(),
                        "word": item["word"],
                        "phonetic": item.get("phonetic", ""),
                        "meaning": item["meaning"].strip(),
                        "example": item.get("example", f"It is important to understand {item['word']}."),
                        "level": 0, "hook": 0, "interval": 0,
                        "review_count": 0, "correct_count": 0, "wrong_count": 0,
                        "last_response_time": None, "last_result": None,
                        "next_review": datetime.now()
                    }
                    st.session_state.deck.append(new_item)
                    saved_count += 1

            save_deck()
            st.success(f"✅ Đã thêm **{saved_count} từ** vào Sổ Tay!")
            st.session_state.all_scanned_words = [
                w for w in st.session_state.all_scanned_words 
                if w['word'] not in [x['word'] for x in final_list]
            ]
            time.sleep(0.8)
            st.rerun()

# ============================================================
# 15. TAB SỔ TAY
# ============================================================

elif selected_tab == "📋 Sổ Tay":
    st.subheader("📋 Sổ tay từ vựng")

    if st.session_state.deck:
        total = len(st.session_state.deck)
        due = sum(1 for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= datetime.now())
        mastered = sum(1 for x in st.session_state.deck if x.get("level", 0) == 5 and x.get("hook", 0) == 4)

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Tổng từ", total)
        with col2: st.metric("Cần ôn", due)
        with col3: st.metric("Cấp 5 • Móc 4", mastered)

        st.markdown("---")

        st.session_state.search_filter = st.text_input(
            "🔎 Tìm kiếm từ hoặc nghĩa trong sổ tay:",
            value=st.session_state.search_filter,
            placeholder="Gõ từ tiếng Anh hoặc nghĩa tiếng Việt..."
        ).strip()

        filtered_deck = st.session_state.deck
        if st.session_state.search_filter:
            kw = st.session_state.search_filter.lower()
            filtered_deck = [
                x for x in st.session_state.deck
                if kw in x.get("word", "").lower() or kw in x.get("meaning", "").lower()
            ]

        table_data = []
        for item in filtered_deck:
            next_review = item.get("next_review")
            remaining = (next_review - datetime.now()).total_seconds() if isinstance(next_review, datetime) else 0
            status = "🔥 Sẵn sàng ôn!" if remaining <= 0 else f"⏳ {format_remaining(remaining)}"

            correct_count = int(item.get("correct_count", 0))
            wrong_count = int(item.get("wrong_count", 0))
            accuracy_total = correct_count + wrong_count
            accuracy_text = f"{correct_count / accuracy_total * 100:.0f}%" if accuracy_total > 0 else "—"

            level = int(item.get("level", 0))
            hook = int(item.get("hook", 0))

            hook_text = "Cấp 0" if level == 0 else f"Cấp {level} • Móc {hook}/4"
            interval_text = "0 giờ" if level == 0 else format_hours(get_current_interval(item))

            table_data.append({
                "ID": item.get("id"),
                "Từ": item.get("word", "").upper(),
                "Nghĩa": item.get("meaning", ""),
                "Cấp": hook_text,
                "Trạng thái": get_level_name(level),
                "Móc": interval_text,
                "Độ chính xác": accuracy_text,
                "Số lần ôn": item.get("review_count", 0),
                "Tiếp theo": status,
            })

        st.dataframe(table_data, use_container_width=True, hide_index=True)

        st.markdown("---")

        with st.expander("🛠️ Quản lý & Chỉnh sửa chi tiết từng từ"):
            word_options = {f"{x['word'].upper()} - {x['meaning']}": x['id'] for x in st.session_state.deck}
            selected_word_str = st.selectbox("Chọn từ cần sửa / xóa:", options=list(word_options.keys()))

            if selected_word_str:
                selected_id = word_options[selected_word_str]
                target_item = next((x for x in st.session_state.deck if x["id"] == selected_id), None)

                if target_item:
                    col_edit1, col_edit2 = st.columns(2)
                    with col_edit1:
                        new_meaning_val = st.text_input("Sửa Nghĩa tiếng Việt:", value=target_item["meaning"], key=f"edit_m_{selected_id}")
                    with col_edit2:
                        new_example_val = st.text_input("Sửa Cụm từ / Câu ví dụ:", value=target_item["example"], key=f"edit_e_{selected_id}")

                    c_act1, c_act2, c_act3 = st.columns([1, 1, 1])
                    with c_act1:
                        if st.button("💾 Lưu thay đổi", type="primary", key=f"save_item_{selected_id}"):
                            target_item["meaning"] = new_meaning_val.strip()
                            target_item["example"] = new_example_val.strip()
                            save_deck()
                            st.success("✅ Đã cập nhật từ vựng thành công!")
                            time.sleep(0.5)
                            st.rerun()

                    with c_act2:
                        if st.button("🤖 AI Reset Từ Này", key=f"ai_reset_item_{selected_id}"):
                            with st.spinner("🤖 AI đang tạo mới nghĩa & ví dụ..."):
                                prompt_single = f"""
Cung cấp nghĩa tiếng Việt ngắn gọn và 1 câu ví dụ đơn giản cho từ: "{target_item['word']}".
Trả về duy nhất JSON:
{{ "meaning": "nghĩa tiếng việt", "example": "câu ví dụ tiếng anh" }}
"""
                                res = call_llm_api(prompt_single)
                                if res:
                                    try:
                                        res_json = json.loads(res)
                                        target_item["meaning"] = res_json.get("meaning", target_item["meaning"])
                                        target_item["example"] = res_json.get("example", target_item["example"])
                                        save_deck()
                                        st.success(f"✅ Đã reset nghĩa & ví dụ cho từ {target_item['word'].upper()}!")
                                        time.sleep(0.5)
                                        st.rerun()
                                    except Exception: st.error("❌ Lỗi xử lý JSON.")

                    with c_act3:
                        if st.button("🗑️ Xóa từ này", key=f"del_item_{selected_id}"):
                            st.session_state.deck = [x for x in st.session_state.deck if x["id"] != selected_id]
                            save_deck()
                            st.success("✅ Đã xóa từ khỏi Sổ Tay!")
                            time.sleep(0.5)
                            st.rerun()

        st.markdown("---")

        st.markdown("### 🤖 AI Reset Lại Nghĩa & Ví Dụ Cho Toàn Bộ Sổ Tay")
        if st.button("⚡ AI RESET LẠI TOÀN BỘ SỔ TAY", type="primary", use_container_width=True, key="ai_bulk_reset"):
            with st.spinner("🤖 AI đang làm mới nghĩa và ví dụ cho toàn bộ từ..."):
                all_words = [x["word"] for x in st.session_state.deck]
                prompt_bulk = f"""
Cung cấp bản dịch nghĩa tiếng Việt ngắn gọn và 1 câu ví dụ ngắn cho danh sách từ sau:
{json.dumps(all_words)}

Trả về kết quả duy nhất dạng JSON Array:
[
  {{ "word": "từ tiếng anh", "meaning": "nghĩa việt", "example": "câu ví dụ" }}
]
Chỉ trả về JSON thô.
"""
                res = call_llm_api(prompt_bulk)
                if res:
                    try:
                        updated = json.loads(res)
                        up_dict = {x["word"].lower(): x for x in updated}
                        for item in st.session_state.deck:
                            w = item["word"].lower()
                            if w in up_dict:
                                item["meaning"] = up_dict[w].get("meaning", item["meaning"])
                                item["example"] = up_dict[w].get("example", item["example"])
                        save_deck()
                        st.success("🎉 Đã reset thành công nghĩa & ví dụ cho toàn bộ Sổ Tay!")
                        time.sleep(0.8)
                        st.rerun()
                    except Exception: st.error("❌ Lỗi phân tích JSON từ AI.")

        st.markdown("---")

        st.markdown("### 📐 Hệ thống Thời Điểm Vàng")
        hook_table = {
            "Cấp 0": "0h — Từ mới",
            "Cấp 1": "1h → 4h → 12h → 24h",
            "Cấp 2": "25h → 28h → 36h → 48h",
            "Cấp 3": "49h → 52h → 60h → 72h",
            "Cấp 4": "73h → 76h → 84h → 96h",
            "Cấp 5": "97h → 100h → 108h → 120h",
        }
        st.table([{"Cấp": level_name, "Các móc": hooks} for level_name, hooks in hook_table.items()])

        st.markdown("---")

        if st.button("🔄 RESET ALL VỀ CẤP 0", use_container_width=True, key="reset_all_words"):
            reset_all_to_level_zero()
            st.success("✅ Đã reset toàn bộ từ về Cấp 0.")
            time.sleep(0.5)
            st.rerun()

        st.markdown("---")

        if st.button("🗑️ Xóa toàn bộ từ vựng", key="delete_all_words"):
            st.session_state.deck = []
            st.session_state.review_item = None
            st.session_state.review_started = False
            st.session_state.q_type = None
            st.session_state.q_data = {}
            st.session_state.temp_word = None
            save_deck()
            st.success("Đã xóa toàn bộ dữ liệu.")
            time.sleep(0.5)
            st.rerun()

    else:
        st.info("📚 Sổ tay đang trống.")

# ============================================================
# 16. FOOTER
# ============================================================

st.markdown("---")
st.caption("🍌 MochiVocab • Dynamic Golden Time")
