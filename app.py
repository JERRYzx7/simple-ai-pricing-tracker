"""AI-Powered PC Pricing Tracker — Streamlit demo.

Joins data/products.csv (static product catalog) with
data/price_snapshots.csv (price time series, keyed by sku) to let the user
pick an equivalence group, see a price comparison table, and see a price
trend chart for that group. Also exposes a lightweight AI chat panel to ask
natural-language questions about the tracked products.
"""

import os

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

PRODUCTS_PATH = "data/products.csv"
SNAPSHOTS_PATH = "data/price_snapshots.csv"

SYSTEM_INSTRUCTION = (
    "You are the AI assistant embedded in the AI-Powered PC Pricing Tracker. "
    "You can only answer questions about the Windows PCs currently tracked in "
    "this app. Always call the provided tools to look up real data before "
    "answering — never invent prices or specs. When comparing products, cite "
    "the actual numbers the tools returned. If something can't be answered "
    "from the tool data, say so plainly instead of guessing. Answer in the "
    "same language the user asked in."
)

load_dotenv()
st.set_page_config(page_title="AI-Powered PC Pricing Tracker", layout="wide")

# --- Global visual design ---------------------------------------------------
# Token system (frontend-design skill: pick deliberately, ground it in the
# subject — a precision price-tracking tool, not a marketing page):
#   surface #14151a / raised #1b1d24 · text #eef0f4 / #9195a8 / #6b6f82
#   accent #9085e9 (violet) — reserved for UI chrome only, deliberately not
#   one of the three chart hues (blue/orange/aqua) so it never reads as
#   "belonging" to one tracked product.
#   Display type: Space Grotesk (headings) · Body/data: Inter (numbers,
#   labels) — two clearly distinct families, per the skill's type guidance.
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
    font-family: 'Space Grotesk', sans-serif !important;
    letter-spacing: -0.01em;
}
.hero-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 2.1rem;
    font-weight: 600;
    letter-spacing: -0.015em;
    margin-bottom: 0.15rem;
    color: #eef0f4;
}
.hero-subtitle {
    color: #9195a8;
    font-size: 0.95rem;
    margin-bottom: 1.6rem;
}
[data-testid="stSidebar"] { border-right: 1px solid rgba(255,255,255,.08); }
[data-testid="stSidebar"] h2 { font-size: 1.05rem; }
[data-testid="stCaptionContainer"], .stCaption { color: #6b6f82 !important; }
hr, [data-testid="stDataFrame"] { border-color: rgba(255,255,255,.08) !important; }
button[kind="primary"], button[kind="secondary"] {
    border-radius: 8px;
}
.stFormSubmitButton button {
    background: #9085e9 !important;
    color: #14151a !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 500;
}
.stFormSubmitButton button:hover { background: #a29bf2 !important; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60)
def load_data():
    if not os.path.isfile(PRODUCTS_PATH) or not os.path.isfile(SNAPSHOTS_PATH):
        return pd.DataFrame()
    products = pd.read_csv(PRODUCTS_PATH, dtype={"sku": str})
    snapshots = pd.read_csv(SNAPSHOTS_PATH, dtype={"sku": str}, parse_dates=["captured_at"])
    return snapshots.merge(products, on="sku", how="inner")


@st.cache_resource
def get_genai_client():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def answer_question(query: str, group_df: pd.DataFrame) -> str:
    """Answer a natural-language question about the currently selected
    equivalence group using Gemini (Google AI Studio free tier) with
    function-calling tools bound to group_df, so the model reasons over
    real data instead of inventing numbers.

    Verified end-to-end: asking "which one is cheapest, and what's the price
    trend?" correctly calls both tools and reports price changes matching
    the CSV exactly (see README.md's known limitations section).
    """
    client = get_genai_client()
    if client is None:
        return (
            "`GOOGLE_API_KEY` isn't set yet. Get a free API key at https://aistudio.google.com/ "
            "and add it to `.env` as `GOOGLE_API_KEY` to enable this feature."
        )

    def list_products() -> list[dict]:
        """List every product in the currently selected equivalence group,
        with its spec description, OS, form factor, retailer, and product URL."""
        cols = ["display_name", "description", "os", "form_factor", "retailer", "url"]
        return group_df[cols].drop_duplicates("display_name").to_dict("records")

    def get_price_summary(product_name: str = "") -> list[dict]:
        """Get price statistics for one product (fuzzy-matched by display
        name) or, if product_name is left empty, for every product in the
        currently selected group: latest price, its source (manual/scraped/
        simulated) and capture time, historical min and max price, the
        first-ever recorded price, percent change from first-seen to
        latest, and how many data points exist."""
        subset = group_df
        if product_name:
            subset = group_df[group_df["display_name"].str.contains(product_name, case=False, na=False)]
        out = []
        for name, g in subset.groupby("display_name"):
            g = g.sort_values("captured_at")
            first, last = g.iloc[0], g.iloc[-1]
            change_pct = ((last["price"] - first["price"]) / first["price"] * 100) if first["price"] else 0.0
            out.append({
                "product": name,
                "latest_price": round(float(last["price"]), 2),
                "latest_price_source": last["source"],
                "latest_captured_at": str(last["captured_at"]),
                "min_price": round(float(g["price"].min()), 2),
                "max_price": round(float(g["price"].max()), 2),
                "first_seen_price": round(float(first["price"]), 2),
                "change_pct_since_first_seen": round(change_pct, 2),
                "data_points": len(g),
            })
        return out

    def get_price_history(product_name: str = "") -> list[dict]:
        """Get the full chronological list of price snapshots — one entry
        per capture, in order — for one product (fuzzy-matched by display
        name) or, if product_name is left empty, for every product in the
        currently selected group. Each entry has the product name,
        captured_at timestamp, price, and source (manual/scraped/simulated).
        Use this for anything get_price_summary's min/max/latest can't
        answer — recent direction, day-over-day change, or any
        window-specific trend, since that tool only gives aggregates over
        the whole history, not the raw sequence."""
        subset = group_df
        if product_name:
            subset = group_df[group_df["display_name"].str.contains(product_name, case=False, na=False)]
        subset = subset.sort_values(["display_name", "captured_at"])
        return [
            {
                "product": row["display_name"],
                "captured_at": str(row["captured_at"]),
                "price": round(float(row["price"]), 2),
                "source": row["source"],
            }
            for _, row in subset.iterrows()
        ]

    # A persistent chat (not a one-shot generate_content call) both avoids the
    # SDK's own "use Chat.send_message instead" warning and gives the model
    # real multi-turn memory across questions. Re-created whenever the set of
    # SKUs in scope changes (group switch or sidebar filter change) so the
    # bound tools always see the current group_df, never a stale one.
    chat_key = tuple(sorted(group_df["sku"].unique()))
    if st.session_state.get("gemini_chat_key") != chat_key:
        st.session_state.gemini_chat = client.chats.create(
            model=os.environ.get("LLM_MODEL", "gemini-3-flash-preview"),
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[list_products, get_price_summary, get_price_history],
            ),
        )
        st.session_state.gemini_chat_key = chat_key

    try:
        response = st.session_state.gemini_chat.send_message(query)
        return response.text
    except Exception as exc:
        return f"Error calling the Gemini API: {exc}"


df = load_data()

st.markdown(
    '<div class="hero-title">AI-Powered PC Pricing Tracker</div>'
    '<div class="hero-subtitle">Comparable Windows PCs on Best Buy — priced and tracked over time.</div>',
    unsafe_allow_html=True,
)

if df.empty:
    st.warning("No price snapshots yet. Run `python fetch_prices.py` first.")
    st.stop()

groups = df[["group_id", "group_name"]].drop_duplicates().sort_values("group_id")
group_id = st.selectbox(
    "Select equivalence group",
    groups["group_id"],
    format_func=lambda gid: groups.set_index("group_id").loc[gid, "group_name"],
)
group_df = df[df["group_id"] == group_id]

# --- Sidebar hardware/price filters (narrow the group's products further) ---
# With only one equivalence group of 3 near-identical-spec products today,
# these filters won't actually exclude much — they're built data-driven and
# reusable so they'd work correctly if more groups/products with varying
# specs get added later, rather than being decorative dead controls.
st.sidebar.header("Filters")


def sidebar_checkbox_filter(label, options, key_prefix):
    """Render one checkbox per option (all checked by default, so the
    untouched/default state shows everything) and return the checked ones.

    If the user unchecks every box in this dimension, treat that as "not
    filtering by this dimension" (show all options) rather than "exclude
    everything" — an empty checkbox group reads as "no constraint," not as
    a filter that matches nothing.
    """
    st.sidebar.markdown(f"**{label}**")
    selected = []
    for opt in options:
        if st.sidebar.checkbox(str(opt), value=True, key=f"{key_prefix}_{opt}"):
            selected.append(opt)
    return selected if selected else list(options)


spec_cols = group_df[["display_name", "form_factor", "processor", "ram_gb", "storage_gb"]].drop_duplicates("display_name")

form_factor_options = sorted(spec_cols["form_factor"].dropna().unique())
selected_form_factors = sidebar_checkbox_filter("Form Factor", form_factor_options, "ff")

processor_options = sorted(spec_cols["processor"].dropna().unique())
selected_processors = sidebar_checkbox_filter("Processor", processor_options, "proc")

ram_options = sorted(spec_cols["ram_gb"].dropna().unique())
selected_ram = sidebar_checkbox_filter("Memory (GB)", ram_options, "ram")

storage_options = sorted(spec_cols["storage_gb"].dropna().unique())
selected_storage = sidebar_checkbox_filter("Storage (GB)", storage_options, "storage")

latest_prices = group_df.sort_values("captured_at").groupby("display_name").tail(1)[["display_name", "price"]]
price_min, price_max = float(latest_prices["price"].min()), float(latest_prices["price"].max())
if price_min < price_max:
    selected_price_range = st.sidebar.slider(
        "Price range (USD, latest price)", min_value=price_min, max_value=price_max, value=(price_min, price_max)
    )
else:
    st.sidebar.caption(f"Price range: ${price_min:.2f} (every product in this group is the same price right now)")
    selected_price_range = (price_min, price_max)

spec_filtered_names = set(spec_cols[
    spec_cols["form_factor"].isin(selected_form_factors)
    & spec_cols["processor"].isin(selected_processors)
    & spec_cols["ram_gb"].isin(selected_ram)
    & spec_cols["storage_gb"].isin(selected_storage)
]["display_name"])
price_filtered_names = set(latest_prices[
    latest_prices["price"].between(selected_price_range[0], selected_price_range[1])
]["display_name"])

group_df = group_df[group_df["display_name"].isin(spec_filtered_names & price_filtered_names)]

if group_df.empty:
    st.info("No products match the current filters — try widening the filters on the left.")
    st.stop()

st.subheader("Current price comparison")
latest = group_df.sort_values("captured_at").groupby("display_name").tail(1)
st.dataframe(
    latest[["display_name", "form_factor", "description", "price", "source", "captured_at"]]
    .rename(columns={"display_name": "product", "price": "price_usd"})
    .reset_index(drop=True)
)

st.subheader("Price trend")
# Fixed categorical order (never cycled) — first 3 slots of the validated
# palette (see dataviz skill: references/palette.md), which pass every CVD
# and normal-vision separation check as a set. One color per product only;
# `source` (manual/scraped/simulated) is carried by marker symbol + hover
# text instead of a second legend-splitting dimension — putting both color
# and symbol on separate variables in px.line explodes the legend into one
# entry per (product, source) combination, which is what looked wrong at first.
CATEGORICAL_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SOURCE_SYMBOL = {"manual": "diamond", "scraped": "circle", "simulated": "circle-open"}
SOURCE_LABEL = {"manual": "Verified manually", "scraped": "Scraped", "simulated": "Simulated"}

product_order = list(spec_cols["display_name"])  # stable order, source-of-truth is products.csv row order
color_map = dict(zip(product_order, CATEGORICAL_PALETTE))  # falls back to Plotly defaults past 8 products

trend_df = group_df.sort_values(["display_name", "captured_at"]).copy()
trend_df["source_label"] = trend_df["source"].map(SOURCE_LABEL).fillna(trend_df["source"])

fig = px.line(
    trend_df,
    x="captured_at",
    y="price",
    color="display_name",
    color_discrete_map=color_map,
    markers=True,
    custom_data=["source_label"],
    labels={"captured_at": "Captured at", "price": "Price (USD)", "display_name": "Product"},
)
fig.update_traces(
    line=dict(width=2),
    marker=dict(size=9),
    hovertemplate="<b>%{fullData.name}</b><br>%{x|%Y-%m-%d %H:%M}<br>$%{y:.2f}<br>Source: %{customdata[0]}<extra></extra>",
)
fig.update_layout(font_family="Inter, -apple-system, sans-serif")
for trace in fig.data:
    sub = trend_df[trend_df["display_name"] == trace.name]
    trace.marker.symbol = [SOURCE_SYMBOL.get(s, "circle") for s in sub["source"]]
st.plotly_chart(fig, use_container_width=True)
st.caption("● Scraped　◆ Verified manually　○ Simulated — source is shown by marker shape; hover a point for details.")

if (group_df["source"] == "simulated").any():
    st.caption("⚠️ Some earlier points on this chart are simulated (source=simulated) placeholder history, not real historical pricing.")

if "chat_open" not in st.session_state:
    st.session_state.chat_open = False
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

bubble = st.container(key="chat_bubble_fab")
with bubble:
    if st.button("✕" if st.session_state.chat_open else "💬", key="chat_toggle_btn"):
        st.session_state.chat_open = not st.session_state.chat_open
        st.rerun()

if st.session_state.chat_open:
    panel = st.container(key="chat_panel")
    with panel:
        st.markdown("**Ask AI**")
        if not st.session_state.chat_messages:
            st.caption("Ask anything about the products in this group.")
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["text"])
        with st.form(key="chat_form", clear_on_submit=True):
            user_query = st.text_input("Question", label_visibility="collapsed", placeholder="Ask a question…")
            submitted = st.form_submit_button("Send")
        if submitted and user_query:
            # Append + rerun immediately so the user's own message shows up
            # right away — otherwise nothing visibly happens until the whole
            # Gemini round-trip finishes, and it looks like sending failed.
            st.session_state.chat_messages.append({"role": "user", "text": user_query})
            st.rerun()

        # A pending user message with no reply yet means we just rerendered
        # after the append above — show a spinner while the API call runs,
        # so "still working" stays visible instead of the UI going quiet.
        if st.session_state.chat_messages and st.session_state.chat_messages[-1]["role"] == "user":
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    reply = answer_question(st.session_state.chat_messages[-1]["text"], group_df)
            st.session_state.chat_messages.append({"role": "assistant", "text": reply})
            st.rerun()

st.markdown(
    """
<style>
.st-key-chat_bubble_fab {
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 1000;
    width: fit-content;
}
.st-key-chat_bubble_fab button {
    border-radius: 50% !important;
    width: 56px;
    height: 56px;
    font-size: 22px;
    background: #9085e9 !important;
    color: #14151a !important;
    border: none !important;
    box-shadow: 0 4px 18px rgba(144,133,233,.45);
}
.st-key-chat_bubble_fab button:hover { background: #a29bf2 !important; }
.st-key-chat_panel {
    position: fixed;
    bottom: 92px;
    right: 24px;
    width: 340px;
    max-height: 60vh;
    overflow-y: auto;
    background: #1b1d24;
    border: 1px solid rgba(144,133,233,.3);
    border-radius: 16px;
    padding: 18px;
    z-index: 999;
    box-shadow: 0 12px 34px rgba(0,0,0,.45);
}
</style>
""",
    unsafe_allow_html=True,
)
