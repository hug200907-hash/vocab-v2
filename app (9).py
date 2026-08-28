import urllib.parse
import streamlit as st

# 1. Định nghĩa html_code trước
html_code = """
<div style="padding: 20px; background-color: #f0f2f6; border-radius: 8px;">
    <h3>Giao diện render bằng st.iframe chuẩn mới</h3>
    <p>Đã thay thế st.components.v1.html theo chuẩn Streamlit 2026.</p>
</div>
"""

# 2. Sử dụng st.iframe trực tiếp (Dùng width='stretch' hoặc số nguyên px)
st.iframe(
    f"data:text/html;charset=utf-8,{urllib.parse.quote(html_code)}",
    width="stretch",
    height=400,
    scrolling=True,
)
