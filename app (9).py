import json
import random
import time
import os
import re
import html
import base64
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import streamlit as st
from streamlit_local_storage import LocalStorage
from openai import OpenAI

# Thư viện mã hóa chuẩn Python
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
# 2. HỆ THỐNG CẤP + MÓC & PHÁT ÂM MAPPING
# ============================================================

LEVEL_HOOKS = {
    1: [1, 4, 12, 24],
    2: [25, 28, 36, 48],
    3: [49, 52, 60, 72],
    4: [73, 76, 84, 96],
    5: [97, 100, 108, 120],
}

LEVEL_SPEED = {
    1: 0.75,
    2: 1.00,
    3: 1.25,
    4: 1.50,
    5: 1.75,
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
    "sync_key": "",
    "auto_merge_neutral_preview": None,
    "auto_merge_neutral_undo_backup": None,
    "show_answer_result": False, # Quản lý hiển thị màn hình kết quả trung gian
    "result_data": {},          # Lưu trữ thông tin kết quả vừa trả lời
    "tts_played_for_result": False # Đánh dấu chống lặp Autoplay khi Streamlit rerun
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
# 4. HÀM PHÁT ÂM (TTS) & TỐC ĐỘ THEO LEVEL (FIXED)
# ============================================================

def get_pronunciation_speed(level):
    """Lấy tốc độ đọc chuẩn dựa trên level của từ (1 -> 5)"""
    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    lvl = max(1, min(5, lvl))
    return LEVEL_SPEED.get(lvl, 1.0)

def get_pronunciation_text(item, level):
    """
    Xác định nội dung phát âm dựa trên level:
    - Level 1-4: Đọc TỪ (item["word"])
    - Level 5: Đọc CẢ CÂU VÍ DỤ (item["example"])
    Nếu Level 5 example rỗng -> Fallback về word
    """
    try:
        lvl = int(level)
    except Exception:
        lvl = 1

    if lvl >= 5:
        ex = item.get("example", "").strip()
        return ex if ex else item.get("word", "").strip()
    else:
        return item.get("word", "").strip()

def fetch_tts_audio_bytes(text):
    """
    Tải trực tiếp MP3 Audio Bytes từ Google TTS Server phía Python backend.
    Kiểm tra kĩ HTTP status, content-type và dung lượng audio.
    """
    clean_text = str(text).strip()
    if not clean_text:
        return None

    encoded_text = urllib.parse.quote(clean_text)
    tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded_text}&tl=en&client=tw-ob"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        req = urllib.request.Request(tts_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status != 200:
                return None
            
            content_type = response.headers.get("Content-Type", "")
            audio_bytes = response.read()

            # Kiểm tra bytes và content-type hợp lệ
            if not audio_bytes or len(audio_bytes) < 500:
                return None
            if "audio" not in content_type and "mpeg" not in content_type and "octet-stream" not in content_type:
                return None

            return audio_bytes
    except Exception:
        return None

def speak_text(text, speed=1.0, auto_play=True, key_suffix=""):
    """
    Phát âm tiếng Anh chuẩn thông qua Streamlit Native Audio (st.audio).
    Tuyệt đối không render audio rỗng, không dùng st.components.v1.html.
    """
    if not text:
        return

    audio_bytes = fetch_tts_audio_bytes(text)

    if not audio_bytes:
        st.warning("⚠️ Không thể tải file phát âm (Vui lòng kiểm tra mạng).")
        return

    # Chuyển audio bytes thành Base64 Data URL ổn định
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    audio_src = f"data:audio/mpeg;base64,{audio_b64}"

    st.markdown(f"🔊 **Phát âm ({speed}x)**")
    
    # Sử dụng st.audio Native của Streamlit
    st.audio(audio_src, format="audio/mp3", autoplay=auto_play)

# ============================================================
# 5. THUẬT TOÁN GỢI Ý (HINT) NGUYÊN ÂM THÔNG MINH
# ============================================================

VOWELS = set("aeiouyAEIOUY")

def extract_vowel_groups(word):
    groups = []
    i = 0
    n = len(word)
    while i < n:
        if word[i] in VOWELS:
            start = i
            v_chars = []
            indices = []
            while i < n and word[i] in VOWELS:
                v_chars.append(word[i])
                indices.append(i)
                i += 1
            groups.append({
                "text": "".join(v_chars),
                "indices": indices,
                "length": len(indices),
                "start_index": start
            })
        else:
            i += 1
    return groups

def rank_vowel_groups(groups):
    return sorted(groups, key=lambda g: (-g["length"], g["start_index"]))

def get_base_hints_by_level(level_tag_or_num):
    tag = str(level_tag_or_num).upper()
    if "A1" in tag or tag == "1":
        return 1
    elif "A2" in tag or tag == "2":
        return 2
    elif "B1" in tag or tag == "3":
        return 2
    elif "B2" in tag or tag == "4":
        return 3
    elif "C1" in tag or tag == "5":
        return 3
    elif "C2" in tag:
        return 4
    return 1

def build_smart_vowel_hint(word, level_tag=1, wrong_count=0):
    word_str = str(word).strip()
    n = len(word_str)
    if n == 0:
        return ""

    groups = extract_vowel_groups(word_str)
    if not groups:
        mask = [word_str[0]] + ["_"] * (n - 1)
        return " ".join(mask) + f" ({n} ký tự)"

    ranked_groups = rank_vowel_groups(groups)
    num_groups = len(ranked_groups)

    base_hints = get_base_hints_by_level(level_tag)
    total_steps = base_hints + max(0, int(wrong_count))

    revealed_indices = set()

    for step in range(total_steps):
        group_idx = step % num_groups
        cycle_count = step // num_groups
        target_group = ranked_groups[group_idx]

        group_indices = target_group["indices"]
        for idx in group_indices:
            revealed_indices.add(idx)

        if cycle_count > 0:
            extra_reveal = cycle_count
            start_i = target_group["start_index"]
            for offset in range(1, extra_reveal + 1):
                after_idx = group_indices[-1] + offset
                if after_idx < n:
                    revealed_indices.add(after_idx)
                before_idx = start_i - offset
                if before_idx >= 0:
                    revealed_indices.add(before_idx)

    if len(revealed_indices) >= n:
        unrevealed = [i for i in range(n) if i not in revealed_indices]
        if not unrevealed:
            candidates = [i for i in range(n) if word_str[i] not in VOWELS]
            hide_idx = candidates[-1] if candidates else n - 1
            revealed_indices.remove(hide_idx)

    display_chars = []
    for i in range(n):
        if i in revealed_indices:
            display_chars.append(word_str[i])
        else:
            display_chars.append("_")

    hint_pattern = " ".join(display_chars)
    return f"{hint_pattern} ({n} ký tự)"

def get_word_hint(word, level_tag=1, wrong_count=0):
    return build_smart_vowel_hint(word, level_tag=level_tag, wrong_count=wrong_count)

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

# ============================================================
# 6. THÔNG TIN CẤP / MÓC
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
# 7. CHUẨN HÓA ITEM & LOAD/SAVE LOCAL
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
    if isinstance(next_review, datetime):
        item["next_review"] = next_review
    elif isinstance(next_review, str):
        try: item["next_review"] = datetime.fromisoformat(next_review)
        except Exception: item["next_review"] = datetime.now()
    else:
        item["next_review"] = datetime.now()

    item["interval"] = get_current_interval(item)
    return item

if not st.session_state.data_loaded:
    try:
        saved_key = local_storage.getItem("mochi_sync_key")
        if saved_key:
            st.session_state.sync_key = str(saved_key)
        
        saved_data = local_storage.getItem("mochi_deck_data")
        if saved_data:
            items = json.loads(saved_data)
            if isinstance(items, list):
                st.session_state.deck = [normalize_item(x) for x in items if isinstance(x, dict)]
    except Exception:
        st.session_state.deck = []
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
    except Exception:
        pass

def get_next_id():
    if not st.session_state.deck: return 1
    ids = [int(item.get("id", 0)) for item in st.session_state.deck if str(item.get("id", 0)).isdigit()]
    return max(ids) + 1 if ids else 1

# ============================================================
# 8. HỆ THỐNG ĐỒNG BỘ NỘI BỘ
# ============================================================

def generate_sync_key():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choices(chars, k=8))

def get_sync_key():
    key = st.session_state.get("sync_key", "")
    if not key or len(key) != 8:
        try:
            stored_key = local_storage.getItem("mochi_sync_key")
            if stored_key and len(str(stored_key)) == 8:
                key = str(stored_key)
            else:
                key = generate_sync_key()
                local_storage.setItem("mochi_sync_key", key)
        except Exception:
            key = generate_sync_key()
        st.session_state.sync_key = key
    return key

def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(passphrase.encode("utf-8"))

def export_sync_file(key, deck_list):
    serializable_deck = []
    for item in deck_list:
        c = dict(item)
        if isinstance(c.get("next_review"), datetime):
            c["next_review"] = c["next_review"].isoformat()
        serializable_deck.append(c)

    payload = {
        "version": 1,
        "sync_key": key,
        "exported_at": datetime.now().isoformat(),
        "deck": serializable_deck
    }
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    
    salt = os.urandom(16)
    nonce = os.urandom(12)
    aes_key = _derive_key(key, salt)
    
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, json_bytes, None)
    
    file_bytes = salt + nonce + ciphertext
    return file_bytes

def validate_and_decrypt_sync_file(file_bytes, current_key):
    try:
        try:
            data = json.loads(file_bytes.decode("utf-8"))
            if data.get("sync_key") != current_key:
                return False, f"❌ File này có mã đồng bộ ({data.get('sync_key')}) KHÔNG khớp với máy này ({current_key}).", None
            return True, "✅ Xác nhận dữ liệu thành công!", data.get("deck", [])
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        if len(file_bytes) < 28:
            return False, "❌ Tập tin bị hỏng hoặc không đúng định dạng.", None

        salt = file_bytes[:16]
        nonce = file_bytes[16:28]
        ciphertext = file_bytes[28:]

        aes_key = _derive_key(current_key, salt)
        aesgcm = AESGCM(aes_key)
        
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        data = json.loads(decrypted_bytes.decode("utf-8"))

        file_key = data.get("sync_key")
        if file_key != current_key:
            return False, f"❌ File này thuộc mã ({file_key}), không khớp với mã hiện tại ({current_key}).", None

        return True, "✅ Đã giải mã và xác nhận mã đồng bộ thành công!", data.get("deck", [])

    except Exception:
        return False, f"❌ Không thể giải mã file. Mã đồng bộ của máy này ({current_key}) không khớp với mã tạo file!", None

def merge_items(local_item, imported_item):
    merged = dict(local_item)
    
    local_lv = int(local_item.get("level", 0))
    imp_lv = int(imported_item.get("level", 0))
    
    if imp_lv > local_lv or (imp_lv == local_lv and int(imported_item.get("hook", 0)) > int(imported_item.get("hook", 0))):
        merged["level"] = imported_item.get("level", merged.get("level"))
        merged["hook"] = imported_item.get("hook", merged.get("hook"))
        merged["interval"] = imported_item.get("interval", merged.get("interval"))
        merged["next_review"] = imported_item.get("next_review", merged.get("next_review"))

    for field in ["review_count", "correct_count", "wrong_count"]:
        merged[field] = max(int(local_item.get(field, 0)), int(imported_item.get(field, 0)))

    for field in ["phonetic", "meaning", "example"]:
        if not merged.get(field) and imported_item.get(field):
            merged[field] = imported_item.get(field)

    return normalize_item(merged)

def merge_decks(local_deck, imported_deck):
    merged_map = {}
    word_to_id = {}

    for item in local_deck:
        norm = normalize_item(item)
        item_id = norm["id"]
        word = norm["word"].lower()
        
        merged_map[item_id] = norm
        if word:
            word_to_id[word] = item_id

    duplicate_count = 0
    added_count = 0

    for item in imported_deck:
        norm = normalize_item(item)
        imp_id = norm["id"]
        word = norm["word"].lower()

        target_id = None
        if imp_id in merged_map:
            target_id = imp_id
        elif word in word_to_id:
            target_id = word_to_id[word]

        if target_id is not None:
            merged_map[target_id] = merge_items(merged_map[target_id], norm)
            duplicate_count += 1
        else:
            if imp_id in merged_map or imp_id == 0:
                new_id = max(list(merged_map.keys()) + [0]) + 1
                norm["id"] = new_id
            
            merged_map[norm["id"]] = norm
            if word:
                word_to_id[word] = norm["id"]
            added_count += 1

    final_deck = list(merged_map.values())
    return final_deck, len(local_deck), len(imported_deck), duplicate_count, len(final_deck)

# ============================================================
# 9. TÍCH HỢP LLM API & ADAPTIVE EXAMPLE GENERATOR
# ============================================================

def call_llm_api(prompt, api_key=None):
    active_key = api_key or st.secrets.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")

    if not active_key:
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
    except Exception:
        return None

def map_level_to_target_audience(level):
    try:
        lvl = int(level)
    except Exception:
        lvl = 1

    if lvl <= 1:
        return "LEVEL 1/5 (Người mới/trẻ nhỏ): Câu rất ngắn, từ vựng cơ bản, cấu trúc đơn đơn giản, tình huống cụ thể dễ hình dung."
    elif lvl == 2:
        return "LEVEL 2/5 (Tiểu học): Vẫn dễ hiểu, dài hơn L1, thêm thời gian/nơi chốn/nguyên nhân đơn giản, có thể dùng and, but, because, when."
    elif lvl == 3:
        return "LEVEL 3/5 (THCS): Cấu trúc rõ ràng, dùng because, although, while, when, if, before, after, mệnh đề quan hệ đơn giản."
    elif lvl == 4:
        return "LEVEL 4/5 (THPT): Complex sentences, relative clauses, conditional structures, participle clauses khi phù hợp, diễn đạt ý phức tạp hơn."
    else:
        return "LEVEL 5/5 (Học thuật cao): Complex clauses, academic vocabulary, formal expressions, collocations, cấu trúc lồng nhau, ý tưởng trừu tượng nhưng vẫn tự nhiên."

def generate_adaptive_example(item, new_level):
    word = item.get("word", "").strip()
    meaning = item.get("meaning", "").strip()
    prev_example = item.get("example", "").strip()
    audience_req = map_level_to_target_audience(new_level)

    prompt = f"""
Bạn là chuyên gia biên soạn giáo trình tiếng Anh thích ứng (Adaptive Learning).
Hãy tạo MỘT câu ví dụ tiếng Anh mới cho từ mục tiêu (target word).

THÔNG TIN TỪ MỤC TIÊU:
- Target Word: "{word}" (Phải dùng chính xác từ này, không thay bằng synonym)
- Nghĩa đang học: "{meaning}" (Giữ đúng nghĩa này, không đổi sang nghĩa khác)
- Cấp độ yêu cầu: Level {new_level}/5
- Yêu cầu độ khó: {audience_req}
- Câu ví dụ cũ (previous_example): "{prev_example}"

CÁC QUY TẮC BẮT BUỘC:
1. TARGET WORD PHẢI ĐƯỢC DÙNG DÙNG ĐÚNG CHÍNH TẢ: "{word}".
2. ĐỘ KHÓ PHẢI TĂNG/GIẢM BẰNG CHẤT LƯỢNG (Vocabulary, Grammar, Sentence structure, Logical relationships) đúng chuẩn Level {new_level}/5.
3. KHÔNG TẠO EXAMPLE CHUNG CHUNG.
4. KHÔNG LẶP LẠI EXAMPLE CỦA LẦN TRƯỚC.

YÊU CẦU ĐẦU RA:
Trả về DUY NHẤT JSON thô (không markdown, không giải thích):
{{
  "example": "Câu ví dụ tiếng Anh mới hoàn chỉnh ở đây"
}}
"""
    res = call_llm_api(prompt)
    if res:
        try:
            res_data = json.loads(res)
            new_ex = res_data.get("example", "").strip()
            if new_ex and len(new_ex) > 3:
                return new_ex
        except Exception:
            pass
    return None

# ============================================================
# 10. TRA TỪ & DICTIONARY API
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

# ============================================================
# 11. TẠO CÂU HỎI TĨNH CHO TAB ÔN TẬP
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
# 12. TIẾN / LÙI MÓC & XỬ LÝ ĐÁP ÁN
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
    new_level = int(item["level"])

    with st.spinner("🤖 AI đang điều chỉnh câu ví dụ theo trình độ..."):
        new_example = generate_adaptive_example(item, new_level)
        if new_example:
            item["example"] = new_example

    save_deck()

    st.session_state.result_data = {
        "is_correct": is_correct,
        "correct_ans_text": correct_ans_text,
        "word": item["word"],
        "phonetic": item.get("phonetic", ""),
        "meaning": item.get("meaning", ""),
        "example": item.get("example", ""),
        "level": new_level,
        "item": dict(item)
    }
    st.session_state.show_answer_result = True
    st.session_state.tts_played_for_result = False # Reset cờ Autoplay cho kết quả mới
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
    st.session_state.show_answer_result = False
    st.session_state.tts_played_for_result = False
    save_deck()

# ============================================================
# 13. HEADER & ĐỒNG BỘ BẰNG FILE
# ============================================================

current_sync_key = get_sync_key()

st.title("🍌 MochiVocab")
st.caption("Dynamic Golden Time • Học theo cấp và 4 móc ghi nhớ")

with st.expander("☁️ Đồng bộ dữ liệu"):
    st.subheader("🔑 Mã đồng bộ của bạn")
    
    col_k1, col_k2 = st.columns([3, 1])
    with col_k1:
        st.code(current_sync_key, language=None)
    with col_k2:
        new_k_input = st.text_input("Đổi Key (nếu chuyển sang máy mới):", max_chars=8, placeholder="Key 8 số...").strip().upper()
        if st.button("Áp dụng Key mới"):
            if len(new_k_input) == 8:
                st.session_state.sync_key = new_k_input
                local_storage.setItem("mochi_sync_key", new_k_input)
                st.success("✅ Đã cập nhật Key!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.warning("⚠️ Key phải gồm 8 ký tự.")

    st.caption("Sao chép mã này để nhập vào các máy khác của bạn.")
    st.markdown("---")

    col_sync1, col_sync2 = st.columns(2)

    with col_sync1:
        st.markdown("### 📤 Xuất dữ liệu")
        st.write("Tạo file đồng bộ đã mã hóa bằng mã Key của bạn.")
        
        sync_file_bytes = export_sync_file(current_sync_key, st.session_state.deck)
        filename = f"mochivocab_sync_{current_sync_key}.mochi"

        st.download_button(
            label="⬇️ Tải file đồng bộ",
            data=sync_file_bytes,
            file_name=filename,
            mime="application/octet-stream",
            use_container_width=True
        )

    with col_sync2:
        st.markdown("### 📥 Nhập dữ liệu")
        uploaded_file = st.file_uploader("Chọn file đồng bộ (.mochi):", type=["mochi", "json"], key="sync_uploader")

        if uploaded_file is not None:
            file_bytes = uploaded_file.read()
            is_valid, msg, imported_deck = validate_and_decrypt_sync_file(file_bytes, current_sync_key)
            
            if not is_valid:
                st.error(msg)
            else:
                st.success(msg)
                local_copy = [dict(x) for x in st.session_state.deck]
                merged_deck, count_local, count_imp, count_dup, count_final = merge_decks(local_copy, imported_deck)
                
                st.session_state.deck = merged_deck
                save_deck()

                st.success("✅ Gộp dữ liệu thành công!")
                st.info(
                    f"• Dữ liệu hiện tại: **{count_local}**\n\n"
                    f"• Dữ liệu từ file: **{count_imp}**\n\n"
                    f"• Trùng lặp (đã gộp): **{count_dup}**\n\n"
                    f"• Sau khi gộp: **{count_final}** từ"
                )
                time.sleep(1.5)
                st.rerun()

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
# 14. TAB ÔN TẬP
# ============================================================

if selected_tab == "⏰ Ôn Tập":
    st.subheader("⏰ Ôn tập đúng Thời Điểm Vàng")
    now = datetime.now()
    due_items = [x for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= now]

    if not st.session_state.deck:
        st.warning("📚 Sổ tay đang trống.")
        st.write("Hãy sang **🔍 Tra Từ Mới** hoặc **📄 Quét Bài Đọc** để thêm từ.")

    elif not due_items and not st.session_state.show_answer_result:
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

    else:
        # ----------------------------------------------------
        # UI KẾT QUẢ TRUNG GIAN SAU KHI TRẢ LỜI
        # ----------------------------------------------------
        if st.session_state.show_answer_result:
            res_data = st.session_state.get("result_data", {})
            is_correct = res_data.get("is_correct", False)
            word = res_data.get("word", "")
            phonetic = res_data.get("phonetic", "")
            meaning = res_data.get("meaning", "")
            example = res_data.get("example", "")
            level = res_data.get("level", 1)
            item = res_data.get("item", {})

            # Lấy speed và text phát âm chuẩn theo level mới
            speed = get_pronunciation_speed(level)
            text_to_speak = get_pronunciation_text(item, level)

            # Đảm bảo Autoplay chỉ kích hoạt duy nhất 1 lần khi vừa render màn hình Kết quả
            should_auto_play = not st.session_state.get("tts_played_for_result", False)
            if should_auto_play:
                st.session_state.tts_played_for_result = True

            # UI Kết Quả
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

                # Cụm TTS: Native Audio Streamlit phát âm ổn định
                speak_text(text_to_speak, speed=speed, auto_play=should_auto_play, key_suffix="result")

                st.markdown("---")
                
                # NÚT ▶ TIẾP TỤC BẮT BUỘC
                if st.button("▶ TIẾP TỤC", type="primary", use_container_width=True, key="btn_continue_next"):
                    st.session_state.show_answer_result = False
                    st.session_state.result_data = {}
                    st.session_state.tts_played_for_result = False # Reset cờ phát âm
                    
                    # Chuẩn bị câu hỏi tiếp theo
                    due_now = [x for x in st.session_state.deck if x.get("next_review") and x["next_review"] <= datetime.now()]
                    if due_now:
                        min_level = min(x.get("level", 0) for x in due_now)
                        candidates = [x for x in due_now if x.get("level", 0) == min_level]
                        next_item = random.choice(candidates)
                        prepare_review_question(next_item)
                    else:
                        st.session_state.review_started = False
                        st.session_state.review_item = None
                    st.rerun()

        # ----------------------------------------------------
        # BẮT ĐẦU HOẶC HIỂN THỊ CÂU HỎI REVIEW
        # ----------------------------------------------------
        elif not st.session_state.review_started:
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

                st.write("Chọn nghĩa tiếng Việt:")
                for index, option in enumerate(q_data.get("options", [])):
                    if st.button(option, key=f"choice_{item['id']}_{index}"):
                        process_answer(option.strip().lower() == item["meaning"].strip().lower(), item["meaning"])

            # 2. FILL BLANK
            elif q_type == "FILL_BLANK":
                st.markdown("### ✏️ ĐIỀN TỪ VÀO CHỖ TRỐNG")
                st.info(f"**{q_data.get('sentence', '')}**")
                
                hint = get_word_hint(item['word'], level_tag=item.get("level", 1), wrong_count=item.get("wrong_count", 0))
                st.caption(f"💡 Gợi ý nguyên âm: `{hint}`")

                user_ans = st.text_input("Từ còn thiếu:", key=f"fill_{item['id']}")
                if st.button("Xác Nhận", type="primary", key=f"fill_submit_{item['id']}"):
                    process_answer(user_ans.strip().lower() == item["word"].strip().lower(), item["word"].upper())

            # 3. SPELLING
            elif q_type == "SPELLING":
                st.markdown("### ✍️ LUYỆN CHÍNH TẢ")
                st.info(f"Nghĩa tiếng Việt: **{item['meaning'].upper()}**")

                hint = get_word_hint(item['word'], level_tag=item.get("level", 1), wrong_count=item.get("wrong_count", 0))
                st.caption(f"💡 Gợi ý nguyên âm: `{hint}`")

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
# 15. TAB TRA TỪ MỚI
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

        # Nút nghe thử khi tra từ
        speak_text(data['word'], speed=1.0, auto_play=False, key_suffix="lookup")

        manual_meaning = st.text_input("Chỉnh sửa nghĩa tiếng Việt:", value=data.get("meaning", ""), key=f"manual_m_{data['word']}")
        manual_example = st.text_area("Chỉnh sửa câu ví dụ:", value=data.get("example", ""), height=70, key=f"manual_e_{data['word']}")

        data["meaning"] = manual_meaning.strip()
        data["example"] = manual_example.strip()
        st.session_state.temp_word = data

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
# 16. TAB QUÉT BÀI ĐỌC
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
# 17. TAB SỔ TAY
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

        col_btn_merge, col_btn_undo = st.columns([2, 1])
        with col_btn_merge:
            if st.button("🤖 Tự động gộp từ trung lập", use_container_width=True, key="btn_trigger_auto_merge"):
                neutral_items = [x for x in st.session_state.deck if int(x.get("level", 0)) == 0]
                total_neutral = len(neutral_items)

                if total_neutral == 0:
                    st.info("ℹ️ Không có từ trung lập nào cần gộp.")
                    st.session_state.auto_merge_neutral_preview = None
                else:
                    groups = {}
                    for item in neutral_items:
                        key = item.get("word", "").strip().lower()
                        if key:
                            groups.setdefault(key, []).append(item)

                    mergable_items_count = sum(len(group) for group in groups.values() if len(group) > 1)
                    non_mergable_items_count = total_neutral - mergable_items_count

                    st.session_state.auto_merge_neutral_preview = {
                        "total_neutral": total_neutral,
                        "mergable": mergable_items_count,
                        "non_mergable": non_mergable_items_count,
                        "groups": groups
                    }

        with col_btn_undo:
            if st.session_state.get("auto_merge_neutral_undo_backup") is not None:
                if st.button("↩️ Hoàn tác", use_container_width=True, key="btn_undo_merge"):
                    st.session_state.deck = [dict(x) for x in st.session_state.auto_merge_neutral_undo_backup]
                    st.session_state.auto_merge_neutral_undo_backup = None
                    save_deck()
                    st.success("✅ Đã khôi phục lại trạng thái dữ liệu trước khi gộp!")
                    time.sleep(0.8)
                    st.rerun()

        preview_data = st.session_state.get("auto_merge_neutral_preview")
        if preview_data:
            st.info(
                f"🤖 **Tìm thấy:**\n\n"
                f"• Từ trung lập: **{preview_data['total_neutral']}**\n"
                f"• Có thể gộp: **{preview_data['mergable']}**\n"
                f"• Không thể gộp: **{preview_data['non_mergable']}**"
            )

            col_cf1, col_cf2 = st.columns(2)
            with col_cf1:
                if st.button("[ Tiếp tục gộp ]", type="primary", use_container_width=True, key="btn_confirm_merge"):
                    st.session_state.auto_merge_neutral_undo_backup = [dict(x) for x in st.session_state.deck]

                    non_neutral_deck = [x for x in st.session_state.deck if int(x.get("level", 0)) > 0]
                    
                    merged_neutral_deck = []
                    for word_key, group in preview_data["groups"].items():
                        if len(group) == 1:
                            merged_neutral_deck.append(group[0])
                        else:
                            base_item = group[0]
                            for other_item in group[1:]:
                                base_item = merge_items(base_item, other_item)
                            merged_neutral_deck.append(base_item)

                    new_deck = non_neutral_deck + merged_neutral_deck
                    st.session_state.deck = new_deck
                    save_deck()

                    merged_count = preview_data["mergable"]
                    unchanged_count = preview_data["non_mergable"]
                    before_count = preview_data["total_neutral"]
                    after_count = len(new_deck)

                    st.session_state.auto_merge_neutral_preview = None
                    st.success(
                        f"✅ **Đã gộp từ trung lập**\n\n"
                        f"• Trước: {before_count}\n"
                        f"• Đã gộp: {merged_count}\n"
                        f"• Không thay đổi: {unchanged_count}\n"
                        f"• Sau khi gộp: {after_count}"
                    )
                    time.sleep(1.2)
                    st.rerun()

            with col_cf2:
                if st.button("[ Hủy ]", use_container_width=True, key="btn_cancel_merge"):
                    st.session_state.auto_merge_neutral_preview = None
                    st.rerun()

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
            st.session_state.show_answer_result = False
            st.session_state.tts_played_for_result = False
            save_deck()
            st.success("Đã xóa toàn bộ dữ liệu.")
            time.sleep(0.5)
            st.rerun()

    else:
        st.info("📚 Sổ tay đang trống.")

# ============================================================
# 18. FOOTER
# ============================================================

st.markdown("---")
st.caption("🍌 MochiVocab • Dynamic Golden Time (100% Offline Sync)")
