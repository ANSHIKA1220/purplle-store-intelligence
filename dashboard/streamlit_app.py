"""
Store Intelligence Dashboard — Apex Retail

Real-time analytics from CCTV-derived events.
Auto-refreshes every 10 seconds.

Run: streamlit run dashboard/streamlit_app.py
"""

import os
import time
import requests
import streamlit as st

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
REFRESH_INTERVAL = 10

ZONE_DISPLAY_NAMES = {
    "CENTER_AISLE": "Center Display",
    "LEFT_SHELF": "Left Shelf",
    "RIGHT_SHELF": "Right Shelf",
}


def display_zone_name(zone):
    return ZONE_DISPLAY_NAMES.get(zone, zone)

st.set_page_config(
    page_title="Store Intelligence — Apex Retail",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def api_get(path: str):
    try:
        r = requests.get(f"{BASE_URL}{path}", timeout=5)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "API offline"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.title("🏪 Store Intelligence")
st.sidebar.caption("Apex Retail Analytics Platform")

# Discover available stores
stores_data, stores_err = api_get("/stores")
if stores_err:
    st.error(f"**Cannot reach API** — {stores_err}")
    st.code("uvicorn app.main:app --reload", language="bash")
    st.stop()

store_list = stores_data.get("stores", [])
store_ids = [s["store_id"] for s in store_list]

if not store_ids:
    st.warning("No stores with data yet.")
    st.info("Load data:\n```\npython scripts/load_pos.py\npython scripts/load_sample_events.py\n```")
    st.stop()

selected = st.sidebar.selectbox("Store", options=store_ids, index=0)
meta = next((s for s in store_list if s["store_id"] == selected), {})

if meta.get("latest_event"):
    ts_display = meta["latest_event"][:19].replace("T", " ")
    st.sidebar.caption(f"Last event: {ts_display} UTC")
st.sidebar.caption(f"Total events: {meta.get('event_count', 0):,}")

st.sidebar.divider()
auto_refresh = st.sidebar.checkbox("Auto-refresh (10s)", value=True)
if st.sidebar.button("🔄 Refresh Now"):
    st.rerun()

# Health — show as a clean indicator, not alarming
st.sidebar.divider()
health, _ = api_get("/health")
if health:
    db_ok = health.get("db_connected", False)
    stale = health.get("stale_feeds", [])
    store_stale = selected in stale

    if db_ok and not store_stale:
        st.sidebar.success("🟢 System healthy")
    elif db_ok and store_stale:
        # Explain stale in context — historical data is always "stale" by wall-clock
        last_ts = health.get("last_event_per_store", {}).get(selected, "")
        if last_ts:
            st.sidebar.info(f"📅 Last event: {last_ts[:10]}\n(Historical dataset — pipeline not live)")
        else:
            st.sidebar.warning("⚠️ No recent events — pipeline may be offline")
    else:
        st.sidebar.error("🔴 DB connection issue")

# ─────────────────────────────────────────────────────────────────────────────
# Page header
# ─────────────────────────────────────────────────────────────────────────────

st.title("🏪 Store Intelligence Dashboard")
st.caption(f"Store: **{selected}** · {time.strftime('%H:%M:%S')}")

# ─────────────────────────────────────────────────────────────────────────────
# KPI row — primary business metrics
# ─────────────────────────────────────────────────────────────────────────────

metrics, m_err = api_get(f"/stores/{selected}/metrics")
cv_data, _ = api_get(f"/cv/summary?store_id={selected}")

if m_err:
    st.error(f"Metrics error: {m_err}")
    metrics = {}

col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)

# From CV pipeline (entries/exits from events table, filtered by store)
entries = cv_data.get("entries", 0) if cv_data else 0
exits = cv_data.get("exits", 0) if cv_data else 0

col1.metric("👥 Visitors",   metrics.get("unique_visitors", 0))
col2.metric("🚶 Store Entries", entries)
col3.metric("🚪 Store Exits", exits)
col4.metric("💰 Revenue",    f"₹{metrics.get('total_revenue', 0):,.0f}")
col5.metric("🛒 Purchases",  metrics.get("total_transactions", 0))
col6.metric("📈 Conversion", f"{metrics.get('conversion_rate', 0):.1f}%")
col7.metric("🔢 Billing Queue", metrics.get("current_queue_depth", 0))
col8.metric("🚫 Queue Abandonment", f"{metrics.get('abandonment_rate', 0):.1f}%")

# Contextual hints only when clearly missing data
if metrics.get("total_transactions", 0) == 0 and metrics.get("unique_visitors", 0) > 0:
    st.info("💡 No POS transactions for this store. Run `python scripts/load_pos.py` to load purchase data.")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Funnel + Heatmap
# ─────────────────────────────────────────────────────────────────────────────

left, right = st.columns(2)

with left:
    st.subheader("🔽 Conversion Funnel")
    funnel, f_err = api_get(f"/stores/{selected}/funnel")
    if f_err:
        st.error(f_err)
    elif funnel:
        stages = funnel.get("funnel", [])
        if any(s["count"] > 0 for s in stages):
            import pandas as pd
            df = pd.DataFrame(stages)
            st.bar_chart(df.set_index("label")["count"])
            display = df[["label", "count", "dropoff_pct"]].rename(
                columns={"label": "Stage", "count": "Visitors", "dropoff_pct": "Drop-off %"}
            )
            st.dataframe(display, hide_index=True, use_container_width=True)
        else:
            st.info("No funnel data yet. Ingest ENTRY events to populate.")
        st.metric("Overall Conversion", f"{funnel.get('overall_conversion_pct', 0):.1f}%")

with right:
    st.subheader("🗺️ Zone Heatmap")
    heatmap, h_err = api_get(f"/stores/{selected}/heatmap")
    if h_err:
        st.error(h_err)
    elif heatmap:
        zones = heatmap.get("zones", [])
        confidence = heatmap.get("data_confidence", "LOW")
        sessions = heatmap.get("total_sessions", 0)

        if confidence == "LOW":
            st.info(
                "Demo dataset currently contains limited sessions. "
                "Metrics will become more representative as additional "
                "store traffic is processed."
            )

        if zones:
            import pandas as pd
            df_h = pd.DataFrame(zones)
            df_h["zone_name"] = df_h["zone_name"].apply(display_zone_name)
            st.bar_chart(df_h.set_index("zone_name")["heat_score"])
            display = df_h[["zone_name", "visit_count", "avg_dwell_seconds", "heat_score"]].rename(
                columns={
                    "zone_name": "Zone", "visit_count": "Visits",
                    "avg_dwell_seconds": "Avg Dwell (s)", "heat_score": "Heat Score"
                }
            )
            st.dataframe(display, hide_index=True, use_container_width=True)
        else:
            st.info("No zone data yet.")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Anomalies
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("🚨 Active Anomalies")
anom_data, a_err = api_get(f"/stores/{selected}/anomalies")

if a_err:
    st.error(a_err)
else:
    anom_list = anom_data.get("anomalies", [])
    critical = [a for a in anom_list if a["severity"] == "CRITICAL"]
    warn = [a for a in anom_list if a["severity"] == "WARN"]
    info = [a for a in anom_list if a["severity"] == "INFO"]

    if not anom_list:
        st.success("✅ No active anomalies")
    else:
        for a in critical:
            with st.expander(f"🔴 {a['type']}", expanded=True):
                st.error(a["message"])
                st.caption(f"💡 {a['suggested_action']}")

        for a in warn:
            with st.expander(f"🟡 {a['type']}", expanded=False):
                st.warning(a["message"])
                st.caption(f"💡 {a['suggested_action']}")

        if info:
            with st.expander(f"ℹ️ {len(info)} info alert(s)", expanded=False):
                for a in info:
                    st.info(f"**{a['type']}**: {a['message']}")
                    st.caption(f"💡 {a['suggested_action']}")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Visitor Behaviour — zone activity + dwell, all from main events table
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("📷 CV Pipeline Preview", expanded=False):

    preview_image = "dashboard/assets/cctv_preview.png"

    if os.path.exists(preview_image):
        st.image(
            preview_image,
            caption="Sample CCTV Snapshot",
            use_container_width=True
        )
    else:
        st.info(
            "Camera preview placeholder. Future versions "
            "can display live CCTV snapshots."
        )

st.subheader("📊 Visitor Behaviour Analytics")

col_z, col_d = st.columns(2)

with col_z:
    st.markdown("**Zone Visit Counts**")
    zone_data, _ = api_get(f"/cv/zones?store_id={selected}")
    if zone_data:
        import pandas as pd
        df_z = pd.DataFrame(
            [
                {
                    "Zone": display_zone_name(k),
                    "Visits": v
                }
                for k, v in zone_data.items()
            ]
        ).sort_values("Visits", ascending=False)
        if not df_z.empty:
            st.bar_chart(df_z.set_index("Zone"))
        else:
            st.caption("No zone visits recorded yet.")
    else:
        st.caption("No zone data.")

with col_d:
    st.markdown("**Average Dwell Time Per Zone (Seconds)**")
    dwell_data, _ = api_get(f"/cv/dwell?store_id={selected}")
    if dwell_data:
        import pandas as pd
        df_d = pd.DataFrame(
            [
                {
                    "Zone": display_zone_name(k),
                    "Avg Dwell (s)": v
                }
                for k, v in dwell_data.items()
            ]
        ).sort_values("Avg Dwell (s)", ascending=False)
        if not df_d.empty:
            st.bar_chart(df_d.set_index("Zone"))
        else:
            st.caption("No dwell data yet — requires ZONE_EXIT events with dwell_ms > 0.")
    else:
        st.caption("No dwell data.")

# Event type breakdown
if cv_data:
    st.markdown("**Event Counts By Type**")
    event_counts = {
        "Entries": cv_data.get("entries", 0),
        "Exits": cv_data.get("exits", 0),
        "Zone Visits": cv_data.get("zone_changes", 0),
        "Dwell Events": cv_data.get("dwell_events", 0),
        "Re-entries": cv_data.get("reentries", 0),
        "Queue Joins": cv_data.get("billing_queue_joins", 0),
        "Queue Abandons": cv_data.get("billing_queue_abandons", 0),
    }
    import pandas as pd
    df_ev = pd.DataFrame(
        [{"Event": k, "Count": v} for k, v in event_counts.items() if v > 0]
    )
    if not df_ev.empty:
        st.bar_chart(df_ev.set_index("Event"))

# ─────────────────────────────────────────────────────────────────────────────
# All stores overview (if multiple stores)
# ─────────────────────────────────────────────────────────────────────────────

if len(store_ids) > 1:
    st.divider()
    st.subheader("🏬 All Stores Overview")
    import pandas as pd
    df_s = pd.DataFrame(store_list).rename(columns={
        "store_id": "Store ID", "event_count": "Events", "latest_event": "Latest Event"
    })
    st.dataframe(df_s, hide_index=True, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# Auto-refresh
# ─────────────────────────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(REFRESH_INTERVAL)
    st.rerun()