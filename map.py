import streamlit as st
import pandas as pd
import pydeck as pdk
try:
    import geopandas as gpd
except ImportError:
    import geopandas_lite as gpd

gpd.options.io_engine = "pyogrio"   # ✅ ensures no Fiona/GDAL dependency

from shapely.geometry import Point
from datetime import datetime, time
from opencage.geocoder import OpenCageGeocode
from dateutil import parser
import os

# ─── CONFIG ──────────────────────────────────────────────────────────────

# Set your Mapbox token as environment variable
os.environ["MAPBOX_API_KEY"] = "pk.eyJ1IjoicnNpZGRpcTIiLCJhIjoiY21nMTRkZ21nMGptNzJrb2phOWpyMmpvZiJ9.qbQ-Pn8jaPA7ABdccrTXsg"

HOURS_CSV = "fbcenc_hourly.csv"
ODM_CSV = "ODM_CAFN_2.csv"
TRACTS_SHP = "cb_2023_37_tract_500k.shp"
OPENCAGE_API_KEY = "f53bdda785074d5499b7a4d29d5acd1f"
geocoder = OpenCageGeocode(OPENCAGE_API_KEY)

# ─── STREAMLIT APP ───────────────────────────────────────────────────────

st.set_page_config(page_title="CAFN Food Pantries", layout="wide")
st.title("CAFN Food Finder")

mode = st.radio("Choose input mode:", ["Address", "ZIP Code"])

user_lat, user_lon, user_geoid = None, None, None

if mode == "Address":
    user_address = st.text_input("Enter your address (e.g., 123 Main St, Raleigh, NC):")
    if user_address:
        try:
            results = geocoder.geocode(user_address)
            if results:
                user_lat = results[0]["geometry"]["lat"]
                user_lon = results[0]["geometry"]["lng"]
                #st.success(f"Geocoded location: {user_lat:.5f}, {user_lon:.5f}")
            else:
                st.error("Could not geocode your address.")
                st.stop()
        except Exception as e:
            st.error(f"Geocoding error: {e}")
            st.stop()

elif mode == "ZIP Code":
    zip_code = st.text_input("Enter your ZIP code:")

hourly_df = pd.read_csv(HOURS_CSV)
hourly_df.columns = hourly_df.columns.str.strip().str.lower()
hourly_df["day"] = hourly_df["day"].str.strip().str.title()

odm_df = pd.read_csv(ODM_CSV)
odm_df.columns = odm_df.columns.str.strip().str.lower()
odm_df["geoid"] = odm_df["geoid"].astype(int)

tracts_gdf = gpd.read_file(TRACTS_SHP).to_crs(epsg=4326)
if tracts_gdf["GEOID"].dtype == object:
    tracts_gdf["GEOID"] = tracts_gdf["GEOID"].astype(int)

if mode == "Address" and user_lat is not None and user_lon is not None:
    user_point = Point(user_lon, user_lat)
    matched_tract = tracts_gdf[tracts_gdf.contains(user_point)]
    if not matched_tract.empty:
        user_geoid = matched_tract.iloc[0]["GEOID"]
        #st.success(f"Matched your location to GEOID {user_geoid}")
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


# 👇 Ask user for preferred travel time threshold
user_threshold = st.number_input(
    "Enter travel time threshold (minutes):",
    min_value=5, max_value=120, value=20, step=5,
    help="Agencies within this travel time will be considered nearby."
)

# 👇 Now use that value dynamically
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
        # Fallback: extend the search window automatically (e.g., +40 min)
        agencies_nearby = odm_df[odm_df["total_traveltime"] <= (user_threshold + 40)]
else:
    agencies_nearby = odm_df


# 🚨 FILTER SECTION ────────────────────────────────────────────────────
df = agencies_nearby.copy()
show_choice_only = st.checkbox("Show only Choice Pantries", value=False)

st.markdown("### Select Categories")
filter_1_vals = sorted(df["filter_1"].dropna().unique())
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

if not filtered_df.empty:
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

if show_choice_only:
    filtered_df = filtered_df[filtered_df["choice"] == 1]

# ─── DISPLAY RESULTS ─────────────────────────────────────────────────
if not filtered_df.empty:
    filtered_df = filtered_df.copy()
    filtered_df["total_traveltime"] = filtered_df["total_traveltime"].round(2)
    filtered_df["total_miles"] = filtered_df["total_miles"].round(2)

    #st.success(f"{len(filtered_df)} pantries match your filters.")
    if mode == "ZIP Code":
        display_cols = ["agency name", "address", "operating hours"]
        unique_df = filtered_df.drop_duplicates(subset=["agency name"])
        st.dataframe(unique_df[display_cols].sort_values("agency name"))
    else:
        display_cols = ["agency name", "address", "operating hours", "contact", "total_traveltime", "total_miles"]
        st.dataframe(filtered_df[display_cols].drop_duplicates().sort_values("total_traveltime"))


    # ─── MAP DISPLAY ────────────────────────────────────────────────
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
    agency_map_df["color_r"] = 255
    agency_map_df["color_g"] = 0
    agency_map_df["color_b"] = 0
    agency_map_df["tooltip"] = (
        "Agency: " + agency_map_df["agency name"] +
        "<br>Travel Time (min): " + agency_map_df["total_traveltime"].astype(str) +
        "<br>Distance (miles): " + agency_map_df["total_miles"].astype(str)
    )

    combined_df = pd.concat([user_df, agency_map_df], ignore_index=True)

    layer = pdk.Layer(
        "ScatterplotLayer",
        combined_df,
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
    st.warning("No agencies found matching your filters.")
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

st.title("🍎 Food Finder Feedback Form")
st.write("Help us make the Food Finder app better! This quick form takes less than 2 minutes. 💬")

# Load credentials from Streamlit Secrets
try:
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    SHEET_ID = st.secrets["google_sheet"]["sheet_id"]
    sheet = client.open_by_key(SHEET_ID).sheet1
    connected = True
except Exception as e:
    connected = False
    st.error("⚠️ Could not connect to Google Sheets. Please check your credentials.")
    st.write(e)

# 1️⃣ About Experience
st.subheader("🧭 1. About Your Experience")
usage = st.radio("How often do you use the Food Finder app?", 
                 ["Daily", "Weekly", "Occasionally", "This is my first time"])
ease = st.radio("How easy is it to find what you’re looking for?",
                ["Very easy", "Somewhat easy", "Neutral", "A bit confusing", "Very hard"])
helpful = st.radio("Did the app help you find food resources near you?",
                   ["Yes, completely", "Partially", "No"])

# 2️⃣ What’s Not Working
st.subheader("🐞 2. What’s Not Working")
issues = st.checkbox("I faced issues or errors")
issue_details = st.text_area("If yes, please describe briefly:", "") if issues else ""
problems = st.multiselect(
    "Were any of the following frustrating?",
    ["Slow loading or map not showing",
     "Wrong or missing pantry info",
     "App crashed or froze",
     "Couldn’t filter by time or location",
     "Couldn’t contact agencies",
     "Other"]
)

# 3️⃣ What Could Be Better
st.subheader("🌟 3. What Could Be Better")
features = st.multiselect(
    "What new features or updates would you like to see?",
    ["Better map accuracy",
     "Live pantry hours or status updates",
     "Directions or travel time",
     "Save favorite locations",
     "Add reviews or feedback on pantries",
     "Other"]
)
satisfaction = st.slider("How satisfied are you overall with the app?", 1, 5, 3)

# 4️⃣ Additional Feedback
st.subheader("💡 4. Anything Else?")
comments = st.text_area("Any other thoughts, ideas, or frustrations?")
contact_opt = st.checkbox("I’d like to be contacted about updates")
email = st.text_input("Enter your email (optional):") if contact_opt else ""

# Submit
if st.button("📤 Submit Feedback"):
    feedback = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        usage, ease, helpful, issues, issue_details,
        ", ".join(problems), ", ".join(features),
        satisfaction, comments, email
    ]

    if connected:
        try:
            sheet.append_row(feedback)
            st.success("✅ Thank you! Your feedback has been recorded successfully.")
        except Exception as e:
            st.error("⚠️ Failed to save to Google Sheets.")
            st.write(e)
    else:
        st.warning("⚠️ Could not connect to Google Sheets. Your feedback wasn’t saved.")

