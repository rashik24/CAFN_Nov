# app.py
import os
import pandas as pd
import streamlit as st
import pydeck as pdk
try:
    import geopandas as gpd
except ImportError:
    import geopandas_lite as gpd

gpd.options.io_engine = "pyogrio"   # ✅ ensures no Fiona/GDAL dependency

from shapely.geometry import Point
from opencage.geocoder import OpenCageGeocode
from dateutil import parser
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ───────────────────────────────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="CAFN Food Pantries", layout="wide")
st.title("CAFN Food Finder")

# Set your Mapbox token as environment variable
os.environ["MAPBOX_API_KEY"] = "pk.eyJ1IjoicnNpZGRpcTIiLCJhIjoiY21nMTRkZ21nMGptNzJrb2phOWpyMmpvZiJ9.qbQ-Pn8jaPA7ABdccrTXsg"

HOURS_CSV   = "cafn_hourly.csv"              # columns: agency,city,address,week,day,hour,window,Name,Latitude,Longitude
ODM_CSV     = "ODM CAFN 2.csv"                 # your precomputed travel times & filters
TRACTS_SHP  = "cb_2023_37_tract_500k.shp"
OPENCAGE_API_KEY = "f53bdda785074d5499b7a4d29d5acd1f"   # (from your code)
geocoder = OpenCageGeocode(OPENCAGE_API_KEY)

# ───────────────────────────────────────────────────────────────────────
# INPUT MODE (Address vs ZIP)
# ───────────────────────────────────────────────────────────────────────

mode = st.radio("Choose input mode:", ["Address", "ZIP Code"])
user_lat, user_lon, user_geoid = None, None, None

# ───────────────────────────────────────────────────────────────────────
# CREDENTIALS / (Optional) DRIVE CHECK (commented—same as your code)
# ───────────────────────────────────────────────────────────────────────
creds_ro = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly"]
)
client_ro = gspread.authorize(creds_ro)

# ───────────────────────────────────────────────────────────────────────
# LOAD DATA
# ───────────────────────────────────────────────────────────────────────
# fbcenc_hourly
hourly_df = pd.read_csv(HOURS_CSV)
hourly_df=hourly_df[hourly_df['day']!='Ist']
hourly_df.columns = hourly_df.columns.str.strip().str.lower()
# Normalize day to Title case (e.g., Monday)
hourly_df["day"] = hourly_df["day"].astype(str).str.strip().str.title()
hourly_df["agency"] = hourly_df["agency"].astype(str).str.strip()

# odm travel-time/categorized dataset
odm_df = pd.read_csv(ODM_CSV)
odm_df.columns = odm_df.columns.str.strip().str.lower()
# normalize columns used for joining/filters
odm_df["agency name"] = odm_df["agency name"].astype(str).str.strip()
odm_df["address"] = odm_df["address"].astype(str).str.strip()
if "geoid" in odm_df.columns:
    odm_df["geoid"] = pd.to_numeric(odm_df["geoid"], errors="coerce").fillna(-1).astype(int)

# Load tracts and ensure lon/lat CRS
tracts_gdf = gpd.read_file(TRACTS_SHP).to_crs(epsg=4326)
if tracts_gdf["GEOID"].dtype == object:
    tracts_gdf["GEOID"] = tracts_gdf["GEOID"].astype(int)

# ───────────────────────────────────────────────────────────────────────
# GEOCODE OR ZIP FILTER
# ───────────────────────────────────────────────────────────────────────
if mode == "Address":
    user_address = st.text_input("Enter your address (e.g., 123 Main St, Raleigh, NC):")
    if user_address:
        try:
            results = geocoder.geocode(user_address)
            if results:
                user_lat = results[0]["geometry"]["lat"]
                user_lon = results[0]["geometry"]["lng"]
            else:
                st.error("Could not geocode your address.")
                st.stop()
        except Exception as e:
            st.error(f"Geocoding error: {e}")
            st.stop()

elif mode == "ZIP Code":
    zip_code = st.text_input("Enter your ZIP code:")

# ───────────────────────────────────────────────────────────────────────
# MATCH ADDRESS → TRACT (GEOID) OR USE ZIP SUBSET
# ───────────────────────────────────────────────────────────────────────
if mode == "Address" and user_lat is not None and user_lon is not None:
    user_point = Point(user_lon, user_lat)
    matched_tract = tracts_gdf[tracts_gdf.contains(user_point)]
    if not matched_tract.empty:
        user_geoid = matched_tract.iloc[0]["GEOID"]
    else:
        st.error("Could not match your location to a census tract.")
        st.stop()
elif mode == "ZIP Code" and zip_code:
    zip_filtered = odm_df[odm_df["zip"].astype(str) == zip_code.strip()]
    if zip_filtered.empty:
        st.warning("No agencies found in that ZIP code.")
        st.stop()
    odm_df = zip_filtered.drop_duplicates(subset=["agency name", "address"])
    user_geoid = None

# ───────────────────────────────────────────────────────────────────────
# TRAVEL TIME THRESHOLD
# ───────────────────────────────────────────────────────────────────────
user_threshold = st.number_input(
    "Enter travel time threshold (minutes):",
    min_value=5, max_value=120, value=20, step=5,
    help="Agencies within this travel time will be considered nearby."
)

if user_geoid is not None:
    agencies_nearby = odm_df[
        (odm_df["geoid"] == user_geoid) &
        (odm_df["total_traveltime"] <= user_threshold)
    ]
    if agencies_nearby.empty:
        st.warning(
            f"No agencies linked to your tract within {user_threshold} minutes. "
            "Searching all nearby agencies instead."
        )
        agencies_nearby = odm_df[odm_df["total_traveltime"] <= (user_threshold + 40)]
else:
    agencies_nearby = odm_df

df = agencies_nearby.copy()

# ───────────────────────────────────────────────────────────────────────
# CORE FILTERS (Choice + Filter1 + Filter2) — unchanged
# ───────────────────────────────────────────────────────────────────────
show_choice_only = st.checkbox("Show only Choice Pantries", value=False)

st.markdown("### Select Categories")
filter_1_vals = sorted(df["filter_1"].dropna().unique()) if "filter_1" in df.columns else []
selected_filter_1 = st.multiselect("", filter_1_vals, label_visibility="collapsed", key="filter_1_multi")

for val in filter_1_vals:
    color = "#1f77b4"
    is_selected = val in selected_filter_1
    st.markdown(
        f"<div style='padding: 6px; background-color:{color if is_selected else '#e0e0e0'}; "
        f"color:white; border-radius:5px; margin-bottom:5px'>{val}</div>",
        unsafe_allow_html=True
    )

filtered_df = df[df["filter_1"].isin(selected_filter_1)] if selected_filter_1 else df.copy()

if not filtered_df.empty and "filter_2" in filtered_df.columns:
    st.markdown("### Select Subcategories")
    filter_2_vals = sorted(filtered_df["filter_2"].dropna().unique())
    selected_filter_2 = st.multiselect("", filter_2_vals, label_visibility="collapsed", key="filter_2_multi")

    for val in filter_2_vals:
        color = "#ff7f0e"
        is_selected = val in selected_filter_2
        st.markdown(
            f"<div style='padding: 6px; background-color:{color if is_selected else '#e0e0e0'}; "
            f"color:white; border-radius:5px; margin-bottom:5px'>{val}</div>",
            unsafe_allow_html=True
        )

    if selected_filter_2:
        filtered_df = filtered_df[filtered_df["filter_2"].isin(selected_filter_2)]

if show_choice_only and "choice" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["choice"] == 1]

# ───────────────────────────────────────────────────────────────────────
# NEW: DAY-ONLY FILTER (from fbcenc_hourly.csv)
# ───────────────────────────────────────────────────────────────────────
st.markdown("### 🗓️ Filter by Operating Day")
unique_days = sorted(hourly_df["day"].dropna().unique())
selected_day = st.selectbox("Select Day", ["Any"] + unique_days, index=0)

if selected_day != "Any":
    # Agencies open on that day (by name only)
    open_agencies = (
        hourly_df.loc[hourly_df["day"] == selected_day, "agency"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
    )
    # Filter the main df by agency name
    # (Normalize both sides to be safe)
    f_names = filtered_df["agency name"].astype(str).str.strip()
    filtered_df = filtered_df[f_names.isin(set(open_agencies))]

# ───────────────────────────────────────────────────────────────────────
# DISPLAY RESULTS
# ───────────────────────────────────────────────────────────────────────
if not filtered_df.empty:
    filtered_df = filtered_df.copy()
    if "total_traveltime" in filtered_df.columns:
        filtered_df["total_traveltime"] = pd.to_numeric(filtered_df["total_traveltime"], errors="coerce").round(2)
    if "total_miles" in filtered_df.columns:
        filtered_df["total_miles"] = pd.to_numeric(filtered_df["total_miles"], errors="coerce").round(2)

    if mode == "ZIP Code":
        display_cols = [c for c in ["agency name", "address", "operating hours"] if c in filtered_df.columns]
        unique_df = filtered_df.drop_duplicates(subset=["agency name"])
        st.dataframe(unique_df[display_cols].sort_values("agency name"))
    else:
        display_cols = [c for c in ["agency name", "address", "operating hours", "contact", "total_traveltime", "total_miles"] if c in filtered_df.columns]
        st.dataframe(filtered_df[display_cols].drop_duplicates().sort_values(display_cols[-2] if "total_traveltime" in display_cols else "agency name"))

    # ───────────────────────────────────────────────────────────────────
    # MAP
    # ───────────────────────────────────────────────────────────────────
    if user_lat and user_lon:
        user_df = pd.DataFrame({
            "name": ["Your Location"],
            "latitude": [user_lat],
            "longitude": [user_lon],
            "color_r": [0], "color_g": [0], "color_b": [255],
            "tooltip": ["Your Location"]
        })
    else:
        user_df = pd.DataFrame(columns=["latitude", "longitude", "color_r", "color_g", "color_b", "tooltip"])

    agency_map_df = filtered_df.copy()
    # Make sure lat/lon exist; otherwise skip plotting layer
    lat_col = None
    lon_col = None
    for c in ["latitude", "lat", "y", "ycoord"]:
        if c in agency_map_df.columns:
            lat_col = c
            break
    for c in ["longitude", "lon", "x", "xcoord"]:
        if c in agency_map_df.columns:
            lon_col = c
            break

    if lat_col and lon_col:
        agency_map_df["color_r"] = 255
        agency_map_df["color_g"] = 0
        agency_map_df["color_b"] = 0
        tt = agency_map_df["total_traveltime"].astype(str) if "total_traveltime" in agency_map_df.columns else ""
        tm = agency_map_df["total_miles"].astype(str) if "total_miles" in agency_map_df.columns else ""
        nm = agency_map_df["agency name"].astype(str) if "agency name" in agency_map_df.columns else ""

        agency_map_df["tooltip"] = (
            "Agency: " + nm +
            (("<br>Travel Time (min): " + tt) if "total_traveltime" in agency_map_df.columns else "") +
            (("<br>Distance (miles): " + tm) if "total_miles" in agency_map_df.columns else "")
        )

        combined_df = pd.concat([user_df, agency_map_df.rename(columns={lat_col: "latitude", lon_col: "longitude"})], ignore_index=True, sort=False)

        layer = pdk.Layer(
            "ScatterplotLayer",
            combined_df.dropna(subset=["longitude", "latitude"]),
            get_position='[longitude, latitude]',
            get_color='[color_r, color_g, color_b]',
            get_radius=250,
            pickable=True,
        )

        view_state = pdk.ViewState(
            longitude=user_lon if user_lon else -79.01,
            latitude=user_lat if user_lat else 35.78,
            zoom=10, pitch=0
        )

        deck = pdk.Deck(
            map_style='mapbox://styles/mapbox/light-v9',
            initial_view_state=view_state,
            layers=[layer],
            tooltip={"html": "{tooltip}", "style": {"color": "white"}}
        )

        st.pydeck_chart(deck)
    else:
        st.info("Map coordinates not available for some agencies; map layer skipped.")
else:
    st.warning("No agencies found matching your filters.")


