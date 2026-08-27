import json
import random
import time
import os
import re
from datetime import datetime, timedelta

import streamlit as st
from streamlit_local_storage import LocalStorage
from openai import OpenAI

# ============================================================
# 1. CẤU HÌNH APP & LOCAL STORAGE
# ============================================================

st.set_page_config(
    page_title="MochiVocab AI Adaptive",
    page_icon="🍌",
    layout="centered"
)

local_storage = LocalStorage()

# Danh sách từ thông dụng (Stop Words) để tự động lọc khi quét bài đọc
STOP_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not", "on", "with",
    "he", "as", "you", "do", "at", "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there", "their", "what", "so", "up",
    "out", "if", "about", "who", "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "no", "just", "him", "know", "take", "people", "into", "year", "your", "good", "some", "could",
    "them", "see", "other", "than", "then", "now", "look", "only", "come", "its", "over", "think",
    "also", "back", "after", "use", "two", "how", "our", "work", "first", "well", "way", "even",
    "new", "want", "because", "any", "these", "give", "day", "most", "us", "more", "such", "than"
}

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
    "scanned_results": []
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
# 4. HELPER & FORMAT
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

def get_hook_hours(item):
    level = int(item.get("level", 0))
    hook = int(item.get("hook", 0))
    if level <= 0: return 0
    hooks = LEVEL_HOOKS.get(level, [])
    if not hooks: return 0
    hook = max(1, min(hook, len(hooks)))
    return hooks[hook - 1]

def get_current_interval(item):
    return get_hook_hours(item)

# ============================================================
# 5. GỢI Ý ĐỘNG (ADAPTIVE HINTING SYSTEM)
# ============================================================

def get_adaptive_hint(word, accuracy, meaning=""):
    word = word.strip()
    length = len(word)
    if length <= 2:
        return f"`{word[0]}...` ({length} ký tự)"

    if accuracy >= 0.8:
        return f"`{word[0]} {'_ ' * (length - 1)}` ({length} ký tự)"
    elif accuracy >= 0.5:
        hint_pattern = f"{word[0]} " + " ".join(["_"] * (length - 2)) + f" {word[-1]}"
        return f"`{hint_pattern}` ({length} ký tự)"
    else:
        pattern = []
        vowels = {'a', 'e', 'i', 'o', 'u'}
        for i, char in enumerate(word):
            if i == 0 or i == length - 1 or char.lower() in vowels:
                pattern.append(char)
            else:
                pattern.append("_")
        hint_str = " ".join(pattern)
        return f"`{hint_str}` ({length} ký tự) • 💡 Nghĩa: **{meaning}**"

# ============================================================
# 6. CHUẨN HÓA ITEM & DỮ LIỆU
# ============================================================

def normalize_item(item):
    item = dict(item)
    try: item["id"] = int(item.get("id", 0))
    except: item["id"] = 0
    item["word"] = str(item.get("word", "")).strip()
    item["phonetic"] = str(item.get("phonetic", "")).strip()
    item["meaning"] = str(item.get("meaning", "")).strip()
    item["example"] = str(item.get("example", "")).strip()

    try: level = int(item.get("level", 0))
    except: level = 0
    item["level"] = max(0, min(MAX_LEVEL, level))

    try: hook = int(item.get("hook", 0))
    except: hook = 0
    item["hook"] = 0 if item["level"] == 0 else max(1, min(HOOKS_PER_LEVEL, hook))

    for field in ["review_count", "correct_count", "wrong_count"]:
        try: item[field] = int(item.get(field, 0))
        except: item[field] = 0

    next_review = item.get("next_review")
    if isinstance(next_review, str):
        try: item["next_review"] = datetime.fromisoformat(next_review)
        except: item["next_review"] = datetime.now()
    elif not isinstance(next_review, datetime):
        item["next_review"] = datetime.now()

    item["interval"] = get_current_interval(item)
    return item

if not st.session_state.data_loaded:
    try:
        saved_data = local_storage.getItem("mochi_deck_data")
        if saved_data:
            items = json.loads(saved_data)
            if isinstance(items, list):
                st.session_state.deck = [normalize_item(x) for x in items if isinstance(x, dict)]
    except: st.session_state.deck = []
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
    except: pass

def get_next_id():
    if not st.session_state.deck: return 1
    ids = [int(x.get("id", 0)) for x in st.session_state.deck if str(x.get("id", 0)).isdigit()]
    return max(ids) + 1 if ids else 1

# ============================================================
# 7. TÍCH HỢP LLM API (DÙNG SDK OPENAI + OPENROUTER)
# ============================================================

def call_llm_api(prompt, api_key=None):
    """
    Gọi OpenRouter API thông qua OpenAI Python SDK với model minimax/minimax-m3:free
    """
    active_key = api_key

    if not active_key:
        try:
            active_key = st.secrets["OPENROUTER_API_KEY"]
        except Exception:
            active_key = None

    if not active_key:
        active_key = os.getenv("OPENROUTER_API_KEY")

    if not active_key:
        st.error(
            "❌ **Chưa tìm thấy OPENROUTER_API_KEY.**\n\n"
            "Vào Streamlit Cloud → Settings → Secrets và thêm:\n"
            '```toml\nOPENROUTER_API_KEY = "sk-or-v1-..."\n```'
        )
        return None

    try:
        # Cấu hình client tương thích OpenRouter
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",[cite: 2]
            api_key=active_key.strip(),[cite: 2]
        )

        response = client.chat.completions.create([cite: 2]
            model="minimax/minimax-m3:free",
            messages=[{"role": "user", "content": prompt}],[cite: 2]
        )

        if response.choices and response.choices[0].message.content:[cite: 2]
            content = response.choices[0].message.content[cite: 2]
            # Loại bỏ markdown ```json ... ``` nếu AI trả về bọc trong code block
            cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
            return cleaned.strip()

        return None

    except Exception as e:
        st.error(f"❌ Lỗi OpenRouter API: {str(e)}")
        return None

# ============================================================
# 8. CÁC HÀM XỬ LÝ DỮ LIỆU TỪ VỰNG & BÀI TẬP
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_dictionary_data(word):
    if not word: return None
    import urllib.request, urllib.parse
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data: return data
    except: pass
    return None

def fetch_word_full_data(word):
    data = fetch_dictionary_data(word)
    if not data:
        return {"success": False}
    first = data[0]
    phonetic = first.get("phonetic", "")
    if not phonetic:
        for p in first.get("phonetics", []):
            if p.get("text"): phonetic = p["text"]; break

    meanings, examples = [], []
    for m in first.get("meanings", []):
        pos = m.get("partOfSpeech", "")
        for d in m.get("definitions", []):
            if d.get("definition"): meanings.append({"type": pos, "definition": d["definition"]})
            if d.get("example"): examples.append(d["example"])

    return {
        "success": True,
        "phonetic": phonetic,
        "meanings": meanings,
        "examples": examples
    }

def generate_ai_adaptive_question(item, accuracy):
    word = item.get("word", "")
    meaning = item.get("meaning", "")

    if accuracy >= 0.8:
        diff_level = "HARD (Cấu trúc học thuật, ngữ cảnh phức tạp)"
    elif accuracy >= 0.5:
        diff_level = "MEDIUM (Câu giao tiếp tự nhiên chuẩn mực)"
    else:
        diff_level = "EASY (Ngữ cảnh đơn giản, trực diện)"

    prompt = f"""
Tạo bài tập cho từ "{word}" (nghĩa: {meaning}). 
Mức độ khó: {diff_level}.

Trả về duy nhất JSON:
{{
  "sentence": "Câu tiếng Anh chứa chỗ trống _____ cho từ {word}",
  "distractors": ["nhiễu 1", "nhiễu 2", "nhiễu 3"]
}}
"""
    res = call_llm_api(prompt)
    if res:
        try: return json.loads(res)
        except: pass
    return {
        "sentence": f"It is very important to understand _____ in this context.",
        "distractors": ["resilience", "adaptation", "innovation"]
    }

def play_audio_script(word):
    safe_word = word.replace("'", "\\'").replace('"', '\\"').replace("\n", " ")
    js_code = f"""
    <script>
    window.speechSynthesis.cancel();
    var msg = new SpeechSynthesisUtterance('{safe_word}');
    msg.lang = 'en-US';
    window.speechSynthesis.speak(msg);
    </script>
    """
    st.components.v1.html(js_code, height=0)

def advance_after_correct(item):
    level, hook = int(item.get("level", 0)), int(item.get("hook", 0))
    if level == 0: level, hook = 1, 1
    elif level < MAX_LEVEL:
        if hook < 4: hook += 1
        else: level += 1; hook = 1
    else: hook = 4
    item["level"] = level; item["hook"] = hook; item["interval"] = get_current_interval(item)

def move_back_after_wrong(item):
    level, hook = int(item.get("level", 0)), int(item.get("hook", 0))
    if level <= 1: hook = max(1, hook - 1)
    else:
        if hook > 1: hook -= 1
        else: level -= 1; hook = 4
    item["level"] = level; item["hook"] = hook; item["interval"] = get_current_interval(item)

def process_answer(is_correct, correct_ans_text):
    item = st.session_state.review_item
    if item is None: return

    old_level, old_hook = int(item.get("level", 0)), int(item.get("hook", 0))
    item["review_count"] = int(item.get("review_count", 0)) + 1

    if is_correct:
        item["correct_count"] = int(item.get("correct_count", 0)) + 1
        advance_after_correct(item)
        st.success("✨ Chính xác!")
        st.success(f"📈 Cấp {old_level}, móc {old_hook}/4 → Cấp {item['level']}, móc {item['hook']}/4")
    else:
        item["wrong_count"] = int(item.get("wrong_count", 0)) + 1
        move_back_after_wrong(item)
        st.error(f"❌ Chưa chính xác. Đáp án đúng: **{correct_ans_text}**")
        st.warning(f"📉 Cấp {old_level}, móc {old_hook}/4 → Cấp {item['level']}, móc {item['hook']}/4")

    new_interval_hours = get_current_interval(item)
    item["next_review"] = datetime.now() + timedelta(hours=new_interval_hours)
    item["interval"] = new_interval_hours
    save_deck()

    st.session_state.review_item = None
    st.session_state.q_type = None
    time.sleep(0.8)
    st.rerun()

# ============================================================
# 9. HEADER & TAB NAVIGATION
# ============================================================

st.title("🍌 MochiVocab AI Adaptive")

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
    "Nav", options=tab_options, format_func=lambda x: tab_labels[x],
    key="active_tab", horizontal=True, label_visibility="collapsed"
)
st.markdown("---")

# ============================================================
# 10. TAB ÔN TẬP
# ============================================================

if selected_tab == "⏰ Ôn Tập":
    st.subheader("⏰ Ôn tập thích ứng (Adaptive Learning)")
    due_items = [x for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= now]

    if not st.session_state.deck:
        st.warning("📚 Sổ tay đang trống. Hãy thêm từ mới trước!")
    elif not due_items:
        next_item = min(st.session_state.deck, key=lambda x: x["next_review"])
        remaining = (next_item["next_review"] - datetime.now()).total_seconds()
        st.success("🎉 Bạn đã hoàn thành tất cả từ ôn tập hiện tại!")
        st.info(f"⏰ Từ tiếp theo: **{next_item['word'].upper()}** trong **{format_remaining(remaining)}**")
    else:
        if not st.session_state.review_started:
            st.success(f"🔥 Có **{len(due_items)} từ** đang chờ ôn tập.")
            if st.button("▶️ BẮT ĐẦU ÔN TẬP", type="primary", use_container_width=True):
                st.session_state.review_started = True
                st.rerun()
        else:
            if st.session_state.review_item is None:
                item = random.choice(due_items)
                st.session_state.review_item = item
                st.session_state.review_start_time = time.time()
                
                c, w = int(item.get("correct_count", 0)), int(item.get("wrong_count", 0))
                accuracy = (c / (c + w)) if (c + w) > 0 else 0.5
                
                ai_q = generate_ai_adaptive_question(item, accuracy)
                st.session_state.q_type = random.choice(["FILL_BLANK", "CHOICE_MEANING"])
                st.session_state.q_data = {
                    "sentence": ai_q.get("sentence"),
                    "distractors": ai_q.get("distractors", []),
                    "accuracy": accuracy
                }
                st.rerun()

            item = st.session_state.review_item
            q_type = st.session_state.q_type
            q_data = st.session_state.q_data
            accuracy = q_data.get("accuracy", 0.5)

            if st.button("⏹️ Dừng ôn tập"):
                st.session_state.review_started = False
                st.session_state.review_item = None
                st.rerun()

            if q_type == "FILL_BLANK":
                st.markdown("### ✏️ ĐIỀN TỪ VÀO CHỖ TRỐNG")
                st.info(f"**{q_data.get('sentence')}**")
                
                hint = get_adaptive_hint(item['word'], accuracy, item['meaning'])
                acc_pct = int(accuracy * 100)
                
                if accuracy >= 0.8:
                    st.caption(f"🔥 **Mức Nâng Cao** (Độ chính xác {acc_pct}%) — Gợi ý tối giản:")
                elif accuracy < 0.5:
                    st.caption(f"🛡️ **Mức Cơ Bản** (Độ chính xác {acc_pct}%) — AI trợ giúp chi tiết:")
                else:
                    st.caption(f"⚡ **Mức Tiêu Chuẩn** (Độ chính xác {acc_pct}%):")
                    
                st.markdown(f"💡 Gợi ý: {hint}")

                user_ans = st.text_input("Gõ từ tiếng Anh:", key=f"fill_{item['id']}")
                if st.button("Xác Nhận", type="primary"):
                    process_answer(user_ans.strip().lower() == item["word"].strip().lower(), item["word"].upper())

            else:
                st.markdown("### 🎲 CHỌN NGHĨA ĐÚNG")
                st.info(f"Từ: **{item['word'].upper()}** `{item.get('phonetic')}`")
                
                opts = [item["meaning"]] + q_data.get("distractors", ["phát triển", "thách thức", "cơ hội"])
                random.shuffle(opts)
                
                for idx, opt in enumerate(opts):
                    if st.button(opt, key=f"opt_{idx}"):
                        process_answer(opt.strip().lower() == item["meaning"].strip().lower(), item["meaning"])

# ============================================================
# 11. TAB TRA TỪ MỚI
# ============================================================

elif selected_tab == "🔍 Tra Từ Mới":
    st.subheader("🔍 Tra cứu & Thêm từ mới")

    with st.form(key="search_form"):
        col_in, col_btn = st.columns([4, 1])
        with col_in:
            word_input = st.text_input("Từ tiếng Anh:", placeholder="Nhập từ cần tra...", label_visibility="collapsed").strip().lower()
        with col_btn:
            submit_search = st.form_submit_button("🔎 Tra Từ", type="primary", use_container_width=True)

    if submit_search and word_input:
        with st.spinner("Đang tra từ..."):
            data = fetch_word_full_data(word_input)
            if not data.get("success"):
                st.error(f"❌ Không tìm thấy từ **{word_input}**")
                st.session_state.temp_word = None
            else:
                examples = data.get("examples", [])
                st.session_state.temp_word = {
                    "word": word_input,
                    "phonetic": data.get("phonetic", ""),
                    "meaning": "",
                    "meanings_list": data.get("meanings", []),
                    "example": examples[0] if examples else f"Example sentence with {word_input}."
                }

    data = st.session_state.get("temp_word")
    if data and data.get("word") == word_input:
        st.markdown("---")
        col_w, col_s = st.columns([3, 1])
        with col_w:
            st.markdown(f"## 📌 **{data['word'].upper()}** `{data.get('phonetic')}`")
        with col_s:
            if st.button("🔊 Nghe", use_container_width=True):
                play_audio_script(data["word"])

        if data.get("meanings_list"):
            with st.expander("📖 Xem các nét nghĩa tiếng Anh chi tiết"):
                for m in data["meanings_list"][:4]:
                    st.write(f"• **[{m.get('type')}]** {m.get('definition')}")

        manual_meaning = st.text_input("Nghĩa tiếng Việt:", value=data.get("meaning", ""), placeholder="Nhập nghĩa tiếng Việt...")
        manual_example = st.text_area("Câu ví dụ:", value=data.get("example", ""), height=70)

        data["meaning"] = manual_meaning.strip()
        data["example"] = manual_example.strip()
        st.session_state.temp_word = data

        if st.button("➕ THÊM VÀO SỔ TAY", type="primary", use_container_width=True):
            if not manual_meaning.strip():
                st.error("⚠️ Vui lòng điền nghĩa tiếng Việt trước khi lưu!")
            else:
                new_item = {
                    "id": get_next_id(),
                    "word": data["word"],
                    "phonetic": data["phonetic"],
                    "meaning": manual_meaning.strip(),
                    "example": manual_example.strip(),
                    "level": 0, "hook": 0, "interval": 0,
                    "review_count": 0, "correct_count": 0, "wrong_count": 0,
                    "next_review": datetime.now()
                }
                st.session_state.deck.append(new_item)
                save_deck()
                st.success(f"✅ Đã thêm **{data['word'].upper()}** vào Sổ Tay!")
                time.sleep(0.5)
                st.rerun()

# ============================================================
# 12. TAB QUÉT BÀI ĐỌC
# ============================================================

elif selected_tab == "📄 Quét Bài Đọc":
    st.subheader("📄 Quét Bài Đọc & Lọc Từ Vựng Theo Trình Độ (A1 - C2)")
    st.caption("AI sẽ phân tích bài đọc, lọc ra các từ vựng thuộc đúng cấp độ bạn chọn và giải nghĩa chuẩn theo ngữ cảnh.")

    input_text = st.text_area(
        "Nhập bài đọc tiếng Anh:",
        placeholder="Dán bài báo, bài nghe/đọc hoặc đoạn văn tiếng Anh vào đây...",
        height=180
    )

    col_b1, col_b2 = st.columns([2, 1])
    with col_b1:
        target_band = st.selectbox(
            "🎯 Chọn trình độ từ vựng muốn lọc:",
            options=[
                "🟢 Level A1 - Nhập môn (Beginner)",
                "🟢 Level A2 - Sơ cấp (Elementary)",
                "🟡 Level B1 / IELTS 4.0 - 5.0 (Trung cấp)",
                "🟡 Level B2 / IELTS 5.5 - 6.5 (Trung cấp cao)",
                "🔴 Level C1 / IELTS 7.0 - 8.0 (Nâng cao)",
                "🔴 Level C2 / IELTS 8.5 - 9.0 (Thành thạo/Chuyên sâu)"
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
Bạn là chuyên gia ngôn ngữ tiếng Anh (CEFR & IELTS). Dựa vào bài đọc sau:
---
{input_text[:2000]}
---

Mục tiêu: Hãy tìm và lọc ra tất cả các từ vựng thuộc đúng trình độ: **{target_band}**.
Loại bỏ các từ thuộc trình độ khác và các từ ĐÃ CÓ trong danh sách bên dưới.

Danh sách từ ĐÃ BIẾT (KHÔNG LẤY):
{json.dumps(existing_words[:100])}

Yêu cầu output:
- Giải nghĩa tiếng Việt NGẮN GỌN, CHÍNH XÁC theo đúng ngữ cảnh của bài đọc trên.
- Trích xuất hoặc tạo 1 câu ví dụ minh họa ngắn gọn.
- Gán nhãn trình độ/Band chính xác của từ đó (VD: A1, A2, B1, B2, C1, C2 hoặc IELTS Band).

Trả về kết quả DUY NHẤT dưới dạng JSON Array:
[
  {{
    "word": "từ tiếng anh",
    "phonetic": "/phiên âm/",
    "meaning": "nghĩa việt chuẩn ngữ cảnh",
    "example": "câu ví dụ ngắn",
    "level_tag": "A2"
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
                        st.session_state.scanned_results = []
                        st.success(f"✅ Đã tìm thấy {len(scanned_data)} từ thuộc trình độ {target_band}!")
                    except Exception:
                        st.error("❌ Lỗi cấu trúc JSON từ AI. Vui lòng bấm thử lại!")
                else:
                    st.error("❌ Không thể kết nối với AI API. Kiểm tra lại API Key!")

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
                        "example": item.get("example", f"Example sentence with {item['word']}."),
                        "level": 0, "hook": 0, "interval": 0,
                        "review_count": 0, "correct_count": 0, "wrong_count": 0,
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
# 13. TAB SỔ TAY
# ============================================================

elif selected_tab == "📋 Sổ Tay":
    st.subheader("📋 Quản Lý Sổ Tay & Reset Nghĩa / Ví Dụ")

    if not st.session_state.deck:
        st.info("📚 Sổ tay của bạn hiện đang trống. Hãy qua tab Tra từ hoặc Quét bài đọc để thêm từ mới!")
    else:
        search_kw = st.text_input("🔍 Tìm kiếm từ:", placeholder="Gõ từ tiếng Anh hoặc nghĩa tiếng Việt...").strip().lower()

        filtered_deck = st.session_state.deck
        if search_kw:
            filtered_deck = [
                x for x in st.session_state.deck 
                if search_kw in x.get("word", "").lower() or search_kw in x.get("meaning", "").lower()
            ]

        st.caption(f"Đang hiển thị **{len(filtered_deck)} / {len(st.session_state.deck)}** từ vựng.")

        table_data = []
        for x in filtered_deck:
            table_data.append({
                "ID": x["id"],
                "Từ vựng": x["word"].upper(),
                "Phiên âm": x.get("phonetic", ""),
                "Nghĩa hiện tại": x["meaning"],
                "Ví dụ": x.get("example", ""),
                "Cấp độ": f"Cấp {x.get('level', 0)}"
            })

        st.dataframe(table_data, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### ⚙️ Thao Tác Reset & Cập Nhật Nghĩa/Ví Dụ")

        st.markdown("##### ✏️ 1. Reset / Sửa Nghĩa & Ví Dụ Cho 1 Từ Cụ Thể")
        
        word_options = {f"{x['id']} - {x['word'].upper()}": x for x in st.session_state.deck}
        selected_key = st.selectbox("Chọn từ cần chỉnh sửa nghĩa/ví dụ:", options=list(word_options.keys()))
        selected_item = word_options[selected_key]

        col_edit1, col_edit2 = st.columns(2)
        with col_edit1:
            new_meaning_input = st.text_input("Nghĩa tiếng Việt mới:", value=selected_item["meaning"], key="edit_m_single")
        with col_edit2:
            new_example_input = st.text_area("Ví dụ mới:", value=selected_item.get("example", ""), height=68, key="edit_e_single")

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("💾 Lưu Thay Đổi Từ Này", type="primary", use_container_width=True):
                for item in st.session_state.deck:
                    if item["id"] == selected_item["id"]:
                        item["meaning"] = new_meaning_input.strip()
                        item["example"] = new_example_input.strip()
                        break
                save_deck()
                st.success(f"✅ Đã cập nhật lại nghĩa & ví dụ cho từ **{selected_item['word'].upper()}**!")
                time.sleep(0.8)
                st.rerun()

        with col_btn2:
            if st.button("🤖 AI Tạo Lại Nghĩa & Ví Dụ Cho Từ Này", use_container_width=True):
                with st.spinner("🤖 AI đang tra và làm mới nghĩa + ví dụ..."):
                    prompt_single = f"""
Hãy cung cấp nghĩa tiếng Việt ngắn gọn, chuẩn xác nhất và 1 câu ví dụ đơn giản cho từ tiếng Anh: "{selected_item['word']}".
Trả về duy nhất JSON format:
{{
  "meaning": "nghĩa tiếng việt chuẩn",
  "example": "câu ví dụ tiếng anh"
}}
"""
                    res = call_llm_api(prompt_single)
                    if res:
                        try:
                            res_json = json.loads(res)
                            for item in st.session_state.deck:
                                if item["id"] == selected_item["id"]:
                                    item["meaning"] = res_json.get("meaning", item["meaning"])
                                    item["example"] = res_json.get("example", item["example"])
                                    break
                            save_deck()
                            st.success(f"✅ AI đã reset thành công Nghĩa & Ví dụ cho từ **{selected_item['word'].upper()}**!")
                            time.sleep(0.8)
                            st.rerun()
                        except:
                            st.error("❌ Lỗi xử lý từ AI.")

        st.markdown("---")

        st.markdown("##### 🔄 2. AI Reset Lại Toàn Bộ Nghĩa & Ví Dụ Trong Sổ Tay")
        st.caption("Tính năng này sẽ gửi danh sách từ vựng trong Sổ Tay lên AI để cập nhật lại 100% nghĩa tiếng Việt và ví dụ minh họa chuẩn xác nhất.")

        if st.button("⚡ AI RESET LẠI NGHĨA & VÍ DỤ TOÀN BỘ SỔ TAY", type="primary", use_container_width=True):
            with st.spinner("🤖 AI đang làm mới toàn bộ nghĩa và ví dụ cho Sổ Tay..."):
                all_words_list = [x["word"] for x in st.session_state.deck]
                
                prompt_bulk = f"""
Hãy tạo bản dịch nghĩa tiếng Việt ngắn gọn và 1 câu ví dụ minh họa ngắn cho danh sách các từ tiếng Anh sau:
{json.dumps(all_words_list)}

Trả về kết quả duy nhất dạng JSON Array:
[
  {{
    "word": "từ tiếng anh",
    "meaning": "nghĩa tiếng việt chuẩn",
    "example": "câu ví dụ ngắn"
  }}
]
Chỉ trả về JSON thô.
"""
                res_bulk = call_llm_api(prompt_bulk)
                if res_bulk:
                    try:
                        updated_items = json.loads(res_bulk)
                        updated_dict = {x["word"].lower(): x for x in updated_items}

                        count = 0
                        for item in st.session_state.deck:
                            w_key = item["word"].lower()
                            if w_key in updated_dict:
                                item["meaning"] = updated_dict[w_key].get("meaning", item["meaning"])
                                item["example"] = updated_dict[w_key].get("example", item["example"])
                                count += 1

                        save_deck()
                        st.success(f"🎉 Đã reset và làm mới thành công nghĩa + ví dụ cho **{count} từ** trong Sổ Tay!")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error("❌ Không thể phân tích dữ liệu từ AI. Vui lòng thử lại!")
                else:
                    st.error("❌ Không thể kết nối tới AI API.")
