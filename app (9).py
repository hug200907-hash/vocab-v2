import streamlit as st
import json
import os
import random
import urllib.parse
from datetime import datetime, timedelta

# ==========================================
# 1. CẤU HÌNH TRANG STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Flashcard Ôn Tập Từ Vựng",
    page_icon="🗂️",
    layout="wide"
)

# ==========================================
# 2. CẤU HÌNH PHÁT ÂM (TTS) & SRS
# ==========================================
SRS_INTERVALS = {
    1: timedelta(hours=1),
    2: timedelta(days=1),
    3: timedelta(days=3),
    4: timedelta(days=7),
    5: timedelta(days=14),
    6: timedelta(days=30),
}

def get_pronunciation_speed(level):
    """
    Trả về tốc độ đọc tùy theo Level của từ vựng.
    Level 1-2: 0.7x (chậm)
    Level 3-4: 0.85x (vừa)
    Level 5+: 1.0x (bình thường)
    """
    if level <= 2:
        return 0.7
    elif level <= 4:
        return 0.85
    else:
        return 1.0

def get_pronunciation_text(item, level):
    """
    Trả về văn bản phát âm:
    - Level < 5: Đọc từ vựng
    - Level >= 5: Đọc câu ví dụ (nếu có)
    """
    if level >= 5 and item.get("example"):
        return item.get("example")
    return item.get("word", "")

# ==========================================
# 3. KHỞI TẠO SESSION STATE
# ==========================================
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
    "sync_key": "",
    "auto_merge_neutral_preview": None,
    "auto_merge_neutral_undo_backup": None,
    "show_answer_result": False,  # Quản lý hiển thị màn hình kết quả trung gian
    "result_data": {},           # Lưu trữ thông tin kết quả vừa trả lời
    "tts_played_for_result": False, # TRẠNG THÁI TTS: Đảm bảo chỉ autoplay đúng 1 lần
}

for key, val in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ==========================================
# 4. HÀM PHÁT ÂM TTS TỐI ƯU
# ==========================================
def speak_text(text, speed=1.0, auto_play=True):
    """
    Phát âm tiếng Anh thông qua Google Translate TTS API / HTML5 Audio.
    Đảm bảo đặt playbackRate đúng thời điểm và xử lý lỗi Autoplay mượt mà.
    """
    if not text:
        return
    
    clean_text = str(text).strip()
    encoded_text = urllib.parse.quote_plus(clean_text)
    tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded_text}&tl=en&client=tw-ob"

    audio_id = f"tts_audio_{abs(hash(clean_text))}"
    should_autoplay = "true" if auto_play else "false"

    html_code = f"""
    <div style="margin: 8px 0;">
        <audio id="{audio_id}" src="{tts_url}" preload="auto" style="width: 100%; height: 40px; display: none;"></audio>
        <button id="btn_{audio_id}" onclick="playTTS_{audio_id}()" style="
            background-color: #28a745;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            font-size: 14px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        ">
            🔊 PHÁT ÂM
        </button>
        <span id="err_{audio_id}" style="color: #d9534f; font-size: 12px; display: none; margin-left: 8px;">
            ⚠️ Không thể tự động phát. Hãy bấm nút 🔊 PHÁT ÂM để nghe lại.
        </span>
    </div>

    <script>
        (function() {{
            var audio = document.getElementById('{audio_id}');
            var errSpan = document.getElementById('err_{audio_id}');
            var targetSpeed = {speed};
            var autoPlayReq = {should_autoplay};

            function applySpeedAndPlay() {{
                if (!audio) return;
                audio.playbackRate = targetSpeed;
                if (autoPlayReq) {{
                    var promise = audio.play();
                    if (promise !== undefined) {{
                        promise.catch(function(error) {{
                            console.log("Autoplay blocked by browser:", error);
                            if (errSpan) errSpan.style.display = "inline";
                        }});
                    }}
                }}
            }}

            window.playTTS_{audio_id} = function() {{
                if (!audio) return;
                audio.playbackRate = targetSpeed;
                audio.currentTime = 0;
                audio.play().catch(function(e) {{
                    console.log("Manual play failed:", e);
                }});
            }};

            if (audio.readyState >= 1) {{
                applySpeedAndPlay();
            }} else {{
                audio.addEventListener('loadedmetadata', applySpeedAndPlay);
            }}
        }})();
    </script>
    """
    st.components.v1.html(html_code, height=55)

# ==========================================
# 5. LƯU & TẢI DỮ LIỆU JSON
# ==========================================
DATA_FILE = "flashcards_data.json"

def save_data():
    try:
        serializable_deck = []
        for card in st.session_state.deck:
            c = dict(card)
            if isinstance(c.get("next_review"), datetime):
                c["next_review"] = c["next_review"].isoformat()
            if isinstance(c.get("last_review"), datetime):
                c["last_review"] = c["last_review"].isoformat()
            serializable_deck.append(c)
            
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable_deck, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Lỗi khi lưu dữ liệu: {e}")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for c in data:
                    if c.get("next_review"):
                        c["next_review"] = datetime.fromisoformat(c["next_review"])
                    if c.get("last_review"):
                        c["last_review"] = datetime.fromisoformat(c["last_review"])
                st.session_state.deck = data
        except Exception as e:
            st.error(f"Lỗi khi tải dữ liệu: {e}")
            st.session_state.deck = []
    st.session_state.data_loaded = True

if not st.session_state.data_loaded:
    load_data()

# ==========================================
# 6. CÁC HÀM QUẢN LÝ FLASHCARD
# ==========================================
def add_card(word, meaning, phonetic="", example=""):
    word = word.strip()
    meaning = meaning.strip()
    if not word or not meaning:
        return False
    
    # Kiểm tra trùng lặp
    for c in st.session_state.deck:
        if c["word"].lower() == word.lower():
            return False

    new_card = {
        "word": word,
        "meaning": meaning,
        "phonetic": phonetic.strip(),
        "example": example.strip(),
        "level": 1,
        "next_review": datetime.now(),
        "last_review": None,
        "history": []
    }
    st.session_state.deck.append(new_card)
    save_data()
    return True

# ==========================================
# 7. TẠO CÂU HỎI ÔN TẬP
# ==========================================
def prepare_review_question(item):
    st.session_state.review_item = item
    st.session_state.review_start_time = datetime.now().timestamp()
    
    # Chọn loại câu hỏi
    q_types = ["multiple_choice", "text_input"]
    st.session_state.q_type = random.choice(q_types)
    
    if st.session_state.q_type == "multiple_choice":
        correct = item["meaning"]
        other_meanings = [c["meaning"] for c in st.session_state.deck if c["word"] != item["word"]]
        
        if len(other_meanings) >= 3:
            distractors = random.sample(other_meanings, 3)
        else:
            distractors = list(set(other_meanings))
            while len(distractors) < 3:
                distractors.append(f"Phương án ngẫu nhiên {len(distractors) + 1}")
                
        options = distractors + [correct]
        random.shuffle(options)
        st.session_state.q_data = {"options": options, "correct": correct}
    else:
        st.session_state.q_data = {"correct": item["meaning"]}

# ==========================================
# 8. XỬ LÝ KẾT QUẢ CÂU TRẢ LỜI
# ==========================================
def process_answer(user_answer):
    item = st.session_state.review_item
    if not item:
        return
    
    correct_ans_text = item["meaning"]
    is_correct = False
    
    if st.session_state.q_type == "multiple_choice":
        is_correct = (user_answer == correct_ans_text)
    else:
        is_correct = (user_answer.strip().lower() == correct_ans_text.strip().lower())
    
    # Cập nhật Level & Thời gian ôn tiếp theo
    old_level = item.get("level", 1)
    if is_correct:
        new_level = min(old_level + 1, 6)
    else:
        new_level = max(1, old_level - 1)
        
    item["level"] = new_level
    item["last_review"] = datetime.now()
    item["next_review"] = datetime.now() + SRS_INTERVALS.get(new_level, timedelta(days=1))
    
    # Đóng gói dữ liệu Result UI & Chuyển sang State Hiển Thị Kết Quả
    st.session_state.result_data = {
        "is_correct": is_correct,
        "correct_ans_text": correct_ans_text if not is_correct else "",
        "word": item["word"],
        "phonetic": item.get("phonetic", ""),
        "meaning": item.get("meaning", ""),
        "example": item.get("example", ""),
        "level": new_level,
        "item": dict(item)
    }
    st.session_state.show_answer_result = True
    st.session_state.tts_played_for_result = False  # Reset cờ TTS cho kết quả mới
    save_data()
    st.rerun()

# ==========================================
# 9. GIAO DIỆN CHÍNH (TABS)
# ==========================================
st.title("🗂️ Flashcard Ôn Tập Từ Vựng Tiếng Anh")

tabs = st.tabs(["⏰ Ôn Tập", "➕ Thêm Từ", "📚 Danh Sách Từ", "📊 Thống Kê"])

# ------------------------------------------
# TAB 1: ⏰ ÔN TẬP
# ------------------------------------------
with tabs[0]:
    due_cards = [
        x for x in st.session_state.deck 
        if x.get("next_review") and x["next_review"] <= datetime.now()
    ]
    
    col_stat1, col_stat2 = st.columns(2)
    col_stat1.metric("Tổng số từ", len(st.session_state.deck))
    col_stat2.metric("Số từ cần ôn ngay", len(due_cards))
    st.markdown("---")

    if not st.session_state.deck:
        st.info("Chưa có từ vựng nào trong kho. Hãy qua tab **➕ Thêm Từ** để thêm từ mới!")
    elif not due_cards and not st.session_state.show_answer_result and not st.session_state.review_started:
        st.success("🎉 Tốt lắm! Bạn đã hoàn thành tất cả các từ cần ôn tập lúc này.")
    else:
        # 1. BẮT ĐẦU ÔN TẬP
        if not st.session_state.review_started and not st.session_state.show_answer_result:
            if st.button("🚀 BẮT ĐẦU ÔN TẬP", type="primary", use_container_width=True):
                st.session_state.review_started = True
                min_level = min(x.get("level", 1) for x in due_cards)
                candidates = [x for x in due_cards if x.get("level", 1) == min_level]
                next_item = random.choice(candidates)
                prepare_review_question(next_item)
                st.rerun()

        # 2. HIỂN THỊ MÀN HÌNH KẾT QUẢ KHI TRẢ LỜI XONG
        elif st.session_state.show_answer_result:
            res_data = st.session_state.get("result_data", {})
            is_correct = res_data.get("is_correct", False)
            word = res_data.get("word", "")
            phonetic = res_data.get("phonetic", "")
            meaning = res_data.get("meaning", "")
            example = res_data.get("example", "")
            level = res_data.get("level", 1)
            item = res_data.get("item", {})

            speed = get_pronunciation_speed(level)
            text_to_speak = get_pronunciation_text(item, level)

            # Cờ Autoplay đảm bảo chỉ tự động phát đúng 1 lần
            auto_play_flag = not st.session_state.get("tts_played_for_result", False)
            if auto_play_flag:
                st.session_state.tts_played_for_result = True

            with st.container():
                st.markdown("<br>", unsafe_allow_html=True)
                if is_correct:
                    st.success("### ✅ Chính xác!")
                else:
                    st.error("### ❌ Chưa đúng")
                    if res_data.get("correct_ans_text"):
                        st.caption(f"Đáp án đúng: **{res_data.get('correct_ans_text')}**")

                st.markdown(f"## **{word}**")
                if phonetic:
                    st.markdown(f"`{phonetic}`")
                
                st.markdown(f"### **{meaning}**")
                
                if example:
                    st.info(f"📖 *{example}*")

                st.caption(f"🔊 *Tốc độ phát âm: {speed}x ({'Đọc câu ví dụ' if level >= 5 else 'Đọc từ'})*")

                # HTML5 TTS Player
                speak_text(text_to_speak, speed=speed, auto_play=auto_play_flag)

                # Nút nghe lại chủ động
                btn_replay_label = "🔊 Nghe lại câu ví dụ" if level >= 5 else "🔊 Nghe lại từ"
                if st.button(btn_replay_label, key="btn_replay_audio"):
                    speak_text(text_to_speak, speed=speed, auto_play=True)

                st.markdown("---")
                
                # Nút ▶ TIẾP TỤC
                if st.button("▶ TIẾP TỤC", type="primary", use_container_width=True, key="btn_continue_next"):
                    st.session_state.show_answer_result = False
                    st.session_state.result_data = {}
                    st.session_state.tts_played_for_result = False
                    
                    due_now = [x for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= datetime.now()]
                    if due_now:
                        min_level = min(x.get("level", 1) for x in due_now)
                        candidates = [x for x in due_now if x.get("level", 1) == min_level]
                        next_item = random.choice(candidates)
                        prepare_review_question(next_item)
                    else:
                        st.session_state.review_started = False
                        st.session_state.review_item = None
                    st.rerun()

        # 3. HIỂN THỊ CÂU HỎI ÔN TẬP
        elif st.session_state.review_started and st.session_state.review_item:
            item = st.session_state.review_item
            st.markdown(f"### Từ vựng: **{item['word']}**")
            if item.get("phonetic"):
                st.markdown(f"Phiên âm: `{item['phonetic']}`")

            # Phát âm tự động khi câu hỏi mới hiển thị
            speed = get_pronunciation_speed(item.get("level", 1))
            text_to_speak = get_pronunciation_text(item, item.get("level", 1))
            speak_text(text_to_speak, speed=speed, auto_play=True)

            st.write("---")

            if st.session_state.q_type == "multiple_choice":
                st.subheader("Chọn nghĩa đúng của từ trên:")
                options = st.session_state.q_data["options"]
                for idx, opt in enumerate(options):
                    if st.button(f"{idx+1}. {opt}", key=f"opt_{idx}", use_container_width=True):
                        process_answer(opt)
            else:
                st.subheader("Nhập nghĩa tiếng Việt của từ trên:")
                user_input = st.text_input("Nghĩa của từ:", key="text_answer_input")
                if st.button("Gửi đáp án", type="primary"):
                    if user_input:
                        process_answer(user_input)
                    else:
                        st.warning("Vui lòng nhập đáp án!")

# ------------------------------------------
# TAB 2: ➕ THÊM TỪ
# ------------------------------------------
with tabs[1]:
    st.subheader("Thêm flashcard từ vựng mới")
    with st.form("add_word_form", clear_on_submit=True):
        new_word = st.text_input("Từ tiếng Anh (*):")
        new_phonetic = st.text_input("Phiên âm (Ví dụ: /əˈsɪstənt/):")
        new_meaning = st.text_input("Nghĩa tiếng Việt (*):")
        new_example = st.text_area("Câu ví dụ tiếng Anh:")
        
        submitted = st.form_submit_button("Lưu Flashcard")
        if submitted:
            if add_card(new_word, new_meaning, new_phonetic, new_example):
                st.success(f"Đã thêm thành công từ: **{new_word}")
            else:
                st.error("Thêm thất bại. Hãy chắc chắn bạn đã nhập đủ Từ & Nghĩa, hoặc từ đã tồn tại.")

# ------------------------------------------
# TAB 3: 📚 DANH SÁCH TỪ
# ------------------------------------------
with tabs[2]:
    st.subheader("Danh sách toàn bộ từ vựng")
    
    search = st.text_input("🔎 Tìm kiếm từ hoặc nghĩa:", value=st.session_state.search_filter)
    st.session_state.search_filter = search

    filtered_deck = st.session_state.deck
    if search.strip():
        filtered_deck = [
            c for c in st.session_state.deck 
            if search.lower() in c["word"].lower() or search.lower() in c["meaning"].lower()
        ]

    st.caption(f"Hiển thị {len(filtered_deck)} / {len(st.session_state.deck)} từ")

    for idx, card in enumerate(filtered_deck):
        with st.expander(f"📌 ** - {card['meaning']} (Level {card.get('level', 1)})"):
            st.write(f"**Phiên âm:** {card.get('phonetic', 'N/A')}")
            st.write(f"**Nghĩa:** {card.get('meaning')}")
            st.write(f"**Ví dụ:** {card.get('example', 'N/A')}")
            
            next_rev = card.get("next_review")
            if isinstance(next_rev, datetime):
                st.write(f"**Lần ôn tiếp theo:** {next_rev.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Nghe thử phát âm ngay trong danh sách
            if st.button("🔊 Nghe phát âm", key=f"list_tts_{idx}"):
                sp = get_pronunciation_speed(card.get("level", 1))
                txt = get_pronunciation_text(card, card.get("level", 1))
                speak_text(txt, speed=sp, auto_play=True)

            if st.button("❌ Xóa từ này", key=f"del_{idx}"):
                st.session_state.deck = [c for c in st.session_state.deck if c["word"] != card["word"]]
                save_data()
                st.rerun()

# ------------------------------------------
# TAB 4: 📊 THỐNG KÊ
# ------------------------------------------
with tabs[3]:
    st.subheader("Thống kê lộ trình học")
    if not st.session_state.deck:
        st.info("Chưa có dữ liệu thống kê.")
    else:
        levels_count = {i: 0 for i in range(1, 7)}
        for c in st.session_state.deck:
            lvl = c.get("level", 1)
            levels_count[lvl] = levels_count.get(lvl, 0) + 1

        st.write("### Phân bố từ vựng theo Cấp độ SRS (Level 1 - 6):")
        for lvl in range(1, 7):
            st.progress(
                levels_count[lvl] / max(len(st.session_state.deck), 1), 
                text=f"Level {lvl}: {levels_count[lvl]} từ"
            )
