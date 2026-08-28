import json
import random
import time
import os
import re
import base64
import urllib.parse
import urllib.request
import tempfile
from datetime import datetime, timedelta

import streamlit as st
from streamlit_local_storage import LocalStorage
from openai import OpenAI

# Thư viện mã hóa chuẩn Python
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Thư viện Windows TTS Local (PyTTSx3 / SAPI5)
try:
    import pyttsx3
    HAS_PYTTSX3 = True
except ImportError:
    HAS_PYTTSX3 = False

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

# Mapping Level sang Tốc độ phát âm (PHẦN O)
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
# 3. SESSION STATE (PHẦN S, T)
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
    
    # Session States cho Result UI & Chống gọi lặp (PHẦN S, T)
    "show_answer_result": False,
    "answer_result_item": None,
    "answer_result_correct": False,
    "answer_result_level": 1,
    "answer_result_audio": None,
    "answer_result_audio_text": "",
    "answer_result_audio_speed": 1.0,
    "last_processed_q_id": None, # Chống rerun lặp
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
# 4. MODULE WINDOWS TTS LOCAL (PHẦN L, M, U)
# ============================================================

def generate_tts_audio(text: str, speed: float = 1.0) -> bytes:
    """
    Sử dụng Windows Speech Engine (SAPI5) local hoàn toàn không dùng Internet.
    Xuất audio ra WAV bytes để đưa vào st.audio(). (PHẦN L, M, U)
    """
    if not text or not HAS_PYTTSX3:
        return None

    clean_text = str(text).strip()
    if not clean_text:
        return None

    try:
        # Tạo file tạm thời trên đĩa cứng local
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
            temp_path = fp.name

        engine = pyttsx3.init()
        
        # Thiết lập giọng đọc tiếng Anh
        voices = engine.getProperty("voices")
        for voice in voices:
            if "english" in voice.name.lower() or "en" in voice.id.lower():
                engine.setProperty("voice", voice.id)
                break

        # Tốc độ chuẩn SAPI5 trung bình là 200 WPM
        base_rate = 200
        engine.setProperty("rate", int(base_rate * speed))

        # Lưu audio vào file tạm
        engine.save_to_file(clean_text, temp_path)
        engine.runAndWait()
        engine.stop()

        # Đọc bytes từ file tạm
        with open(temp_path, "rb") as f:
            audio_bytes = f.read()

        # Xóa file tạm sau khi đọc xong
        if os.path.exists(temp_path):
            os.remove(temp_path)

        return audio_bytes
    except Exception as e:
        return None

def get_pronunciation_text(item, level):
    """
    Nội dung đọc theo level mới (PHẦN P):
    - Level 1-4: Đọc TỪ (item["word"])
    - Level 5: Đọc CẢ CÂU VÍ DỤ (item["example"])
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
# 7. CHUẨN HÓA ITEM & LOAD/SAVE LOCAL (PHẦN A, J)
# ============================================================

def normalize_item(item):
    """Sử dụng chính xác cấu trúc dữ liệu từ Sổ Tay (PHẦN A)"""
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
    """Lưu lập tức vào Local Storage (PHẦN J)"""
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
# 8. HỆ THỐNG ĐỒNG BỘ NỘI BỘ (KEY + MÃ HÓA FILE + MERGE 2 CHIỀU)
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
# 9. TÍCH HỢP LLM API
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
            cleaned = re.sub(r"\s*
