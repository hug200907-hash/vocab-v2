import streamlit as st
import streamlit.components.v1 as components

st.title("Test Render HTML")

# 1. Tạo chuỗi HTML an toàn
my_html = """
<div style="background-color: #d4edda; color: #155724; padding: 20px; border-radius: 8px;">
    <h2>Render HTML Thành Công!</h2>
    <p>Nếu bạn nhìn thấy khung màu xanh này, tính năng render HTML vẫn hoạt động bình thường.</p>
</div>
"""

# 2. Render bằng components.html (Cách ổn định nhất hiện tại)
components.html(my_html, height=150)

# 3. Phân cột giao diện Streamlit bên dưới
col1, col2 = st.columns(2)
with col1:
    st.info("Cột trái")
with col2:
    st.success("Cột phải")
