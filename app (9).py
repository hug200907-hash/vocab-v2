import urllib.parse
import streamlit as st
import streamlit.components.v1 as components

# 1. Định nghĩa chuỗi HTML (Phải khai báo TRƯỚC khi gọi iframe)
html_code = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f9f9f9;
            padding: 15px;
            color: #333;
        }
        .card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>Giao diện nhúng từ HTML</h2>
        <p>Đoạn mã này đã được mã hóa an toàn và truyền qua st.components.v1.iframe.</p>
    </div>
</body>
</html>
"""

# 2. Mã hóa mã HTML thành Data URL
encoded_html = urllib.parse.quote(html_code)
data_url = f"data:text/html;charset=utf-8,{encoded_html}"

# 3. Hiển thị bằng components.iframe (quy định rõ width pixel > 0)
components.iframe(
    src=data_url,
    width=700,
    height=400,
    scrolling=True
)

# Các thành phần Streamlit khác tiếp tục bên dưới
col1, col2 = st.columns(2)
with col1:
    st.button("Cột 1")
with col2:
    st.button("Cột 2")
