import base64
import streamlit as st

# Thêm thẻ <meta charset="UTF-8"> vào HTML để hiển thị đúng tiếng Việt và Emoji
html_custom_card = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: Arial, sans-serif;
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

# Mã hóa UTF-8 chuẩn trước khi render
b64_html = base64.b64encode(html_custom_card.encode("utf-8")).decode("utf-8")
data_url = f"data:text/html;charset=utf-8;base64,{b64_html}"

# Render ra giao diện
st.iframe(
    src=data_url,
    width="stretch",
    height=200
)
