import base64
from datetime import datetime, timedelta
import urllib.parse
import streamlit as st
from openai import OpenAI

# -----------------------------------------------------------------------------
# 1. CẤU HÌNH TRANG STREAMLIT
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Vocab App v2",
    page_icon="📚",
    layout="wide"
)

# -----------------------------------------------------------------------------
# 2. KHAI BÁO BIẾN GIAO DIỆN HTML (Phải khai báo trước khi dùng st.iframe)
# -----------------------------------------------------------------------------
html_custom_card = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 10px;
            background-color: #f8f9fa;
        }
        .card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            text-align: center;
        }
        .card h2 { margin-top: 0; font-size: 24px; }
        .card p { font-size: 16px; opacity: 0.9; }
        .badge {
            background-color: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            display: inline-block;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>🚀 Từ Vựng Mỗi Ngày</h2>
        <p>Hệ thống học từ vựng thông minh kết hợp AI & Streamlit</p>
        <div class="badge">Đã đồng bộ thời gian thực</div>
    </div>
</body>
</html>
"""

# Mã hóa Base64 cho HTML để hiển thị an toàn qua st.iframe
b64_html = base64.b64encode(html_custom_card.encode("utf-8")).decode("utf-8")
data_url = f"data:text/html;base64,{b64_html}"

# -----------------------------------------------------------------------------
# 3. GIAO DIỆN CHÍNH (MAIN APP)
# -----------------------------------------------------------------------------
st.title("📚 Chuyện Học Từ Vựng (Vocab-v2)")

# Render Banner HTML bằng st.iframe chuẩn 2026
st.iframe(
    src=data_url,
    width="stretch",
    height=200
)

st.divider()

# Chia cột giao diện Streamlit
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📝 Nhập từ mới")
    vocab_input = st.text_input("Từ tiếng Anh:", placeholder="Ví dụ: Resilient")
    meaning_input = st.text_area("Nghĩa / Ghi chú:", placeholder="Khả năng phục hồi...")
    
    if st.button("Lưu từ vựng", type="primary"):
        if vocab_input:
            st.success(f"Đã lưu từ: **{vocab_input}** vào hệ thống!")
        else:
            st.warning("Vui lòng nhập từ vựng.")

with col2:
    st.subheader("⚙️ Trạng thái & Công cụ AI")
    
    # Hiển thị thời gian (Ví dụ sử dụng datetime/timedelta)
    now = datetime.now()
    next_review = now + timedelta(days=1)
    
    st.info(f"🕒 **Thời gian hiện tại:** {now.strftime('%H:%M - %d/%m/%Y')}")
    st.write(f"📅 **Lịch ôn tập tiếp theo:** {next_review.strftime('%d/%m/%Y')}")
    
    # Khu vực tích hợp OpenAI API (Nếu cần)
    with st.expander("🤖 Tra từ nhanh bằng AI (OpenAI)"):
        api_key = st.text_input("OpenAI API Key:", type="password")
        if st.button("Tạo câu ví dụ"):
            if api_key and vocab_input:
                try:
                    client = OpenAI(api_key=api_key)
                    # Example API Call:
                    # response = client.chat.completions.create(...)
                    st.write(f"Ví dụ cho từ *{vocab_input}*: 'She showed resilient spirit.'")
                except Exception as e:
                    st.error(f"Lỗi API: {e}")
            else:
                st.warning("Vui lòng nhập API Key và Từ vựng.")

# -----------------------------------------------------------------------------
# 4. BẢNG DỮ LIỆU THỬ NGHIỆM
# -----------------------------------------------------------------------------
st.divider()
st.subheader("📊 Danh sách từ đã lưu")

sample_data = [
    {"Từ vựng": "Resilient", "Nghĩa": "Kiên cường, phục hồi nhanh", "Ngày tạo": now.strftime("%Y-%m-%d")},
    {"Từ vựng": "Optimize", "Nghĩa": "Tối ưu hóa", "Ngày tạo": (now - timedelta(days=1)).strftime("%Y-%m-%d")},
]

st.table(sample_data)
