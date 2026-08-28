import base64
import urllib.parse
import streamlit as st

# 1. Định nghĩa HTML
html_code = """
<div style="padding: 20px; background-color: #f0f2f6; border-radius: 8px; font-family: sans-serif;">
    <h3 style="color: #007bff;">Giao diện HTML</h3>
    <p>Đã khắc phục hoàn toàn lỗi crash st.iframe!</p>
</div>
"""

# 2. Mã hóa sang Base64 để tránh lỗi ký tự đặc biệt / độ dài URL
b64_html = base64.b64encode(html_code.encode("utf-8")).decode("utf-8")
data_url = f"data:text/html;base64,{b64_html}"

# 3. Gọi st.iframe (BỎ tham số `scrolling=True` vì phiên bản mới đã mặc định tự xử lý)
st.iframe(
    src=data_url,
    width="stretch",
    height=400,
)
