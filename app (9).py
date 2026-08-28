import streamlit as st
import streamlit.components.v1 as components

# 1. Định nghĩa chuỗi HTML (Phải khai báo TRƯỚC khi gọi components.html)
html_code = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #eef2f5;
            padding: 15px;
            color: #2c3e50;
        }
        .container {
            background: white;
            padding: 20px;
            border-radius: 10px;
            border-left: 5px solid #007bff;
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>Giao diện render trực tiếp với components.html</h2>
        <p>Cách 2 không cần dùng urllib.parse.quote, mã nguồn sạch và trực quan hơn hẳn!</p>
    </div>
</body>
</html>
"""

# 2. Hiển thị trực tiếp nội dung HTML
components.html(
    html_code,
    height=400,
    scrolling=True
)

# Các thành phần Streamlit khác tiếp tục bên dưới
col1, col2 = st.columns(2)
with col1:
    st.button("Cột 1")
with col2:
    st.button("Cột 2")
