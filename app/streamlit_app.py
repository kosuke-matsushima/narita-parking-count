"""検証UI — 解析結果を地図上に表示し、○/✗で評価してDB更新

起動: cd narita-parking-count && streamlit run app/streamlit_app.py
"""
import re
import sys
from datetime import datetime
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from sqlalchemy import text
from streamlit_folium import st_folium

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.config import DATABASE_URL, NARITA_CENTER
from scripts.db import AnalysisSession, DetectedVehicle, ParkingArea, get_session

st.set_page_config(page_title="成田空港 車両カウント", page_icon="P", layout="wide")
st.title("成田空港駐車場 — 車両検出検証システム")

db = get_session()

# --- サイドバー: セッション選択 ---
st.sidebar.header("解析セッション")
sessions = db.query(AnalysisSession).order_by(AnalysisSession.analyzed_at.desc()).all()

if not sessions:
    st.warning("解析セッションがありません。先に detect_vehicles.py を実行してください。")
    st.stop()

opts = {
    f"#{s.id} {s.analyzed_at.strftime('%Y-%m-%d %H:%M') if s.analyzed_at else '?'} ({s.total_detected}台)": s.id
    for s in sessions
}
sel_id = opts[st.sidebar.selectbox("セッション", list(opts.keys()))]

# --- フィルタ ---
st.sidebar.header("フィルタ")
conf_min = st.sidebar.slider("最小信頼度", 0.0, 1.0, 0.10, 0.05)
vf = st.sidebar.radio("検証状態", ["すべて", "未検証", "正解", "誤検出"])

q = db.query(DetectedVehicle).filter(
    DetectedVehicle.session_id == sel_id,
    DetectedVehicle.confidence >= conf_min,
)
if vf == "未検証":
    q = q.filter(DetectedVehicle.is_correct.is_(None))
elif vf == "正解":
    q = q.filter(DetectedVehicle.is_correct.is_(True))
elif vf == "誤検出":
    q = q.filter(DetectedVehicle.is_correct.is_(False))
vehicles = q.order_by(DetectedVehicle.confidence.desc()).all()

# --- 統計 ---
total = db.query(DetectedVehicle).filter(DetectedVehicle.session_id == sel_id).count()
n_ok = db.query(DetectedVehicle).filter(DetectedVehicle.session_id == sel_id, DetectedVehicle.is_correct.is_(True)).count()
n_ng = db.query(DetectedVehicle).filter(DetectedVehicle.session_id == sel_id, DetectedVehicle.is_correct.is_(False)).count()
n_un = total - n_ok - n_ng

c1, c2, c3, c4 = st.columns(4)
c1.metric("検出総数", total)
c2.metric("正解", n_ok)
c3.metric("誤検出", n_ng)
c4.metric("未検証", n_un)
if total > 0:
    st.progress((n_ok + n_ng) / total, text=f"検証進捗: {n_ok + n_ng}/{total}")

# --- 地図 ---
st.subheader("検出結果マップ")
m = folium.Map(location=list(NARITA_CENTER), zoom_start=15, tiles="OpenStreetMap")
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri", name="衛星画像",
).add_to(m)

for area in db.query(ParkingArea).all():
    wkt = db.execute(text(f"SELECT ST_AsText(area) FROM parking_areas WHERE id = {area.id}")).scalar()
    if wkt:
        match = re.search(r"POLYGON\(\((.+)\)\)", wkt)
        if match:
            coords = [[float(lat), float(lon)] for pair in match.group(1).split(",") for lon, lat in [pair.strip().split()]]
            folium.Polygon(coords, color="blue", weight=2, fill=True, fill_opacity=0.1,
                           popup=f"{area.name} (定員:{area.capacity or '?'})").add_to(m)

for v in vehicles:
    wkt = db.execute(text(f"SELECT ST_AsText(location) FROM detected_vehicles WHERE id = {v.id}")).scalar()
    if not wkt:
        continue
    match = re.search(r"POINT\(([^ ]+) ([^ ]+)\)", wkt)
    if not match:
        continue
    lon, lat = float(match.group(1)), float(match.group(2))
    color = "green" if v.is_correct is True else "red" if v.is_correct is False else "orange"
    folium.CircleMarker(
        [lat, lon], radius=4, color=color, fill=True, fill_opacity=0.7,
        popup=f"#{v.id} conf={v.confidence:.0%} {v.class_label}",
    ).add_to(m)

folium.LayerControl().add_to(m)
st_folium(m, width=None, height=600)

# --- 検証テーブル ---
st.subheader("個別検証")
if not vehicles:
    st.info("表示データなし")
else:
    page_sz = 20
    pages = max(1, (len(vehicles) + page_sz - 1) // page_sz)
    pg = st.number_input("ページ", 1, pages, 1)
    page_v = vehicles[(pg - 1) * page_sz: pg * page_sz]

    for v in page_v:
        wkt = db.execute(text(f"SELECT ST_AsText(location) FROM detected_vehicles WHERE id = {v.id}")).scalar()
        match = re.search(r"POINT\(([^ ]+) ([^ ]+)\)", wkt) if wkt else None
        loc = f"({match.group(2)}, {match.group(1)})" if match else "?"
        cols = st.columns([1, 3, 1, 1])
        cols[0].write(f"**#{v.id}**")
        cols[1].write(f"{v.confidence:.0%} {v.class_label} {loc}")
        if cols[2].button("OK", key=f"ok_{v.id}"):
            v.is_correct = True
            v.verified_at = datetime.utcnow()
            v.verified_by = "user"
            db.commit()
            st.rerun()
        if cols[3].button("NG", key=f"ng_{v.id}"):
            v.is_correct = False
            v.verified_at = datetime.utcnow()
            v.verified_by = "user"
            db.commit()
            st.rerun()

    st.divider()
    bc1, bc2 = st.columns(2)
    if bc1.button(f"未検証を全て正解 (conf>={conf_min:.0%})"):
        uv = db.query(DetectedVehicle).filter(
            DetectedVehicle.session_id == sel_id,
            DetectedVehicle.is_correct.is_(None),
            DetectedVehicle.confidence >= conf_min,
        ).all()
        for v in uv:
            v.is_correct = True
            v.verified_at = datetime.utcnow()
            v.verified_by = "bulk"
        db.commit()
        st.rerun()
    if bc2.button("検証リセット"):
        for v in db.query(DetectedVehicle).filter(DetectedVehicle.session_id == sel_id).all():
            v.is_correct = None
            v.verified_at = None
        db.commit()
        st.rerun()

# --- CSV ---
st.sidebar.header("エクスポート")
if st.sidebar.button("CSV"):
    rows = []
    for v in db.query(DetectedVehicle).filter(DetectedVehicle.session_id == sel_id).all():
        wkt = db.execute(text(f"SELECT ST_AsText(location) FROM detected_vehicles WHERE id = {v.id}")).scalar()
        match = re.search(r"POINT\(([^ ]+) ([^ ]+)\)", wkt) if wkt else None
        rows.append({
            "id": v.id, "lat": float(match.group(2)) if match else None,
            "lon": float(match.group(1)) if match else None,
            "confidence": v.confidence, "class": v.class_label,
            "is_correct": v.is_correct,
        })
    st.sidebar.download_button("DL", pd.DataFrame(rows).to_csv(index=False), "vehicles.csv")

db.close()
