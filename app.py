"""Minimal boot test — prove Streamlit Cloud can render ANYTHING from this repo."""
import streamlit as st

st.set_page_config(page_title="Stock Advisor", page_icon="📈", layout="wide")
st.title("📈 Stock Advisor")
st.success("Hello from Streamlit Cloud!")
st.write("If you see this, the deploy works. We'll add features one by one.")

import sys
st.code(f"Python: {sys.version}\nStreamlit: {st.__version__}")
