import streamlit as st
import requests

BASE_URL = "http://127.0.0.1:8000"

st.set_page_config(
    page_title="Purplle Store Intelligence",
    layout="wide"
)

st.title("🛍️ Purplle Store Intelligence Dashboard")

# Metrics

metrics = requests.get(
    f"{BASE_URL}/stores/ST1008/metrics"
).json()

cv = requests.get(
    f"{BASE_URL}/cv/summary"
).json()

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Revenue",
    f"₹{metrics['total_revenue']:.2f}"
)

c2.metric(
    "Transactions",
    metrics["total_transactions"]
)

c3.metric(
    "Entries",
    cv["entries"]
)

c4.metric(
    "Exits",
    cv["exits"]
)

# CV Summary

st.subheader("Computer Vision Analytics")

st.json(cv)

# Zone Activity

zones = requests.get(
    f"{BASE_URL}/cv/zones"
).json()

st.subheader("Zone Activity")

st.bar_chart(zones)

# Dwell Analytics

dwell = requests.get(
    f"{BASE_URL}/cv/dwell"
).json()

st.subheader("Dwell Time")

st.bar_chart(dwell)