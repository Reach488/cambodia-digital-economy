"""
app.py
======
Streamlit dashboard: Cambodia Digital Economy & Cyber Readiness.

The dashboard reads the dataset saved by fetch_data.py
(data/cambodia_econ.csv and data/metadata.json). It never calls the World Bank
API itself, so the page loads quickly and always shows exactly the data that
was validated and analysed. Every number on the page, including the key
insights, is calculated from that file when the page runs.

Run locally (with the virtual environment active):
    streamlit run app.py
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"

COUNTRY_ORDER = ["Cambodia", "Vietnam", "Myanmar", "Laos"]

# One fixed colour per country in every chart, checked for colour-vision
# deficiency. Dark mode uses steps of the same hues tuned for a dark background.
COUNTRY_COLORS = {
    "light": {"Cambodia": "#2a78d6", "Vietnam": "#eb6834", "Myanmar": "#1baf7a", "Laos": "#eda100"},
    "dark": {"Cambodia": "#3987e5", "Vietnam": "#d95926", "Myanmar": "#199e70", "Laos": "#c98500"},
}

GDP_GROWTH = "NY.GDP.MKTP.KD.ZG"
GDP_PER_CAPITA = "NY.GDP.PCAP.CD"
INFLATION = "FP.CPI.TOTL.ZG"
INTERNET_USERS = "IT.NET.USER.ZS"
SECURE_SERVERS = "IT.NET.SECR.P6"

KPI_INDICATORS = [GDP_GROWTH, GDP_PER_CAPITA, INTERNET_USERS, INFLATION]


# ---------------------------------------------------------------------------
# Loading and small helpers
# ---------------------------------------------------------------------------

@st.cache_data
def load_data() -> tuple[pd.DataFrame, dict]:
    """Read the dataset once and keep it in memory between page interactions."""
    df = pd.read_csv(DATA_DIR / "cambodia_econ.csv")
    metadata = json.loads((DATA_DIR / "metadata.json").read_text(encoding="utf-8"))
    return df, metadata


def wide_table(df: pd.DataFrame, indicator_code: str) -> pd.DataFrame:
    """One indicator as a table: rows = years, columns = countries."""
    data = df[df["indicator_code"] == indicator_code]
    table = data.pivot(index="year", columns="country", values="value")
    return table[[country for country in COUNTRY_ORDER if country in table.columns]]


def country_colors() -> dict:
    """Pick the light or dark colour steps to match the viewer's theme."""
    theme = getattr(st.context, "theme", None)
    return COUNTRY_COLORS["dark" if theme is not None and theme.type == "dark" else "light"]


def format_value(value: float, unit: str, markdown: bool = False) -> str:
    """Format a number for display according to its unit."""
    if "US$" in unit:
        dollar = "\\$" if markdown else "$"  # in Markdown, $...$ would start a formula
        return f"{dollar}{value:,.0f}"
    if "%" in unit:
        return f"{value:.1f}%"
    return f"{value:,.1f}"


def year_ranges(years: list[int]) -> str:
    """Compress years into ranges: [2000, 2021, 2022, 2023] -> '2000, 2021-2023'."""
    years = sorted(years)
    ranges, start, previous = [], years[0], years[0]
    for year in years[1:]:
        if year != previous + 1:
            ranges.append((start, previous))
            start = year
        previous = year
    ranges.append((start, previous))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def ordinal(number: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(number, f"{number}th")


# ---------------------------------------------------------------------------
# Section A: introduction
# ---------------------------------------------------------------------------

def show_introduction(df: pd.DataFrame, metadata: dict) -> None:
    st.title("Cambodia Digital Economy & Cyber Readiness Dashboard")
    st.markdown(
        "How have Cambodia's economy and digital adoption developed since 2000, and how "
        "does Cambodia compare with its neighbours? This dashboard explores economic "
        "growth, income, Internet and mobile adoption, and secure Internet servers using "
        "official World Bank data."
    )
    first_year, last_year = metadata["dataset_year_range"]
    retrieved = metadata["retrieved_at_utc"][:10]
    columns = st.columns(3)
    columns[0].markdown(f"**Countries**  \n{', '.join(COUNTRY_ORDER)}")
    columns[1].markdown(f"**Coverage**  \n{first_year}-{last_year} (latest year varies by indicator)")
    columns[2].markdown(f"**Source**  \nWorld Bank, World Development Indicators (retrieved {retrieved})")


# ---------------------------------------------------------------------------
# Section B: Cambodia KPI cards
# ---------------------------------------------------------------------------

def show_kpis(df: pd.DataFrame, metadata: dict) -> None:
    st.header("Cambodia at a glance")
    st.caption("Latest available value of each indicator. Years differ because each "
               "indicator is published on its own schedule.")
    columns = st.columns(len(KPI_INDICATORS))
    for column, code in zip(columns, KPI_INDICATORS):
        info = metadata["indicators"][code]
        series = wide_table(df, code)["Cambodia"].dropna()  # only real observations
        latest_year, latest_value = series.index[-1], series.iloc[-1]
        previous_year, previous_value = series.index[-2], series.iloc[-2]
        change = latest_value - previous_value
        # A change in a percentage is measured in percentage points (pp).
        delta = f"{change:+.1f} pp" if "%" in info["unit"] else f"{change:+,.0f}"
        column.metric(
            label=f"{info['name']}, {latest_year}",  # short, so the year is never cut off
            value=format_value(latest_value, info["unit"]),
            delta=delta,
            delta_color="off",  # neutral grey: a change is not automatically good or bad
            delta_description=f"vs {previous_year}",
            chart_data=series.tolist(),
            border=True,
            help=f"{info['world_bank_name']}, {latest_year}. Line shows {series.index[0]}-{latest_year}.",
        )


# ---------------------------------------------------------------------------
# Section C: trends over time
# ---------------------------------------------------------------------------

def show_trends(df: pd.DataFrame, metadata: dict, countries: list[str]) -> None:
    st.header("Economic and digital trends")
    indicators = metadata["indicators"]
    code = st.selectbox(
        "Indicator",
        options=list(indicators),
        format_func=lambda c: f"{indicators[c]['category']} · {indicators[c]['name']} ({indicators[c]['unit']})",
        index=list(indicators).index(INTERNET_USERS),
    )
    info = indicators[code]
    data = df[(df["indicator_code"] == code) & df["country"].isin(countries)]
    use_log_scale = code == SECURE_SERVERS  # values span 0.02 to thousands

    fig = px.line(
        data, x="year", y="value", color="country", log_y=use_log_scale,
        category_orders={"country": COUNTRY_ORDER}, color_discrete_map=country_colors(),
        labels={"year": "Year", "value": info["unit"], "country": "Country"},
        title=f"{info['name']} ({info['unit']})",
    )
    fig.update_traces(line_width=2, hovertemplate="%{y:,.2f}")
    # Legend in a row above the plot, so the lines keep their full width on phones.
    fig.update_layout(hovermode="x unified", legend=dict(title_text="", orientation="h",
                                                         yanchor="bottom", y=1.0, x=0))
    if use_log_scale:
        fig.update_yaxes(dtick=1)  # label only powers of ten
    st.plotly_chart(fig)

    # Explain gaps instead of hiding them: lines break where no value exists.
    missing = data[data["value"].isna()]
    if missing.empty:
        st.caption("No missing years for this selection.")
    else:
        gaps = "; ".join(
            f"{country}: {year_ranges(missing.loc[missing['country'] == country, 'year'].tolist())}"
            for country in COUNTRY_ORDER if country in set(missing["country"])
        )
        st.caption(f"Gaps in the lines are years with no published value. {gaps}.")
    if use_log_scale:
        st.caption("Logarithmic scale: each grid step is ten times the one below. "
                   "This is a digital infrastructure indicator, not a measure of cyber readiness.")
    if code == "IT.CEL.SETS.P2":
        st.caption("Values above 100 are possible: one person can hold several subscriptions.")

    with st.expander("Definition and data table"):
        st.markdown(f"**{info['world_bank_name']}**: {info['definition']}")
        st.caption(f"Source: {info['source_organization']}")
        st.dataframe(wide_table(df, code)[countries].round(2).rename_axis(index="Year", columns=None))


# ---------------------------------------------------------------------------
# Section D: digital readiness vs income
# ---------------------------------------------------------------------------

def internet_income_pairs(df: pd.DataFrame, countries: list[str]) -> pd.DataFrame:
    """Country-years where both Internet users and GDP per capita exist."""
    data = df[df["indicator_code"].isin([INTERNET_USERS, GDP_PER_CAPITA]) & df["country"].isin(countries)]
    pairs = data.pivot(index=["country", "year"], columns="indicator_code", values="value")
    return pairs.dropna().reset_index()


def show_scatter(df: pd.DataFrame, countries: list[str]) -> None:
    st.header("Digital readiness versus income")
    st.markdown("Each point is one country in one year. Panels share the same axes, so "
                "positions can be compared across countries.")
    pairs = internet_income_pairs(df, countries)

    fig = px.scatter(
        pairs, x=INTERNET_USERS, y=GDP_PER_CAPITA, color="country",
        facet_col="country", facet_col_wrap=2, facet_row_spacing=0.12,
        category_orders={"country": COUNTRY_ORDER}, color_discrete_map=country_colors(),
        hover_name="country", hover_data={"year": True, "country": False},
        labels={INTERNET_USERS: "Internet users (%)",  # short: two panels share one row on phones
                GDP_PER_CAPITA: "GDP per capita (current US$)", "year": "Year"},
        height=650,
    )
    fig.update_traces(marker=dict(size=9))
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))  # "country=Laos" -> "Laos"
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig)

    pearson = pairs[INTERNET_USERS].corr(pairs[GDP_PER_CAPITA])
    per_country = pd.DataFrame({
        "Pearson r": pairs.groupby("country").apply(lambda g: g[INTERNET_USERS].corr(g[GDP_PER_CAPITA])),
        "Country-years (n)": pairs.groupby("country").size(),
    }).reindex([c for c in COUNTRY_ORDER if c in countries]).rename_axis("Country")
    left, right = st.columns([1, 2])
    left.metric("Pearson r, selected countries", f"{pearson:.2f}", border=True,
                help=f"Based on {len(pairs)} country-years with both values.")
    right.dataframe(per_country, column_config={"Pearson r": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Correlation measures association, not cause. Both indicators rise over time in "
               "every country, which by itself produces a high r. Consecutive years of one "
               "country are not independent observations, so r is descriptive only.")


# ---------------------------------------------------------------------------
# Section E: key insights (calculated from the data, not typed in)
# ---------------------------------------------------------------------------

def growth_insight(df: pd.DataFrame) -> str:
    growth = df[df["indicator_code"] == GDP_GROWTH].dropna(subset=["value"])
    averages = growth.groupby("country")["value"].mean().sort_values(ascending=False)
    rank = list(averages.index).index("Cambodia") + 1
    cambodia = growth[growth["country"] == "Cambodia"]
    first, last = cambodia["year"].min(), cambodia["year"].max()
    periods = [(2000, 2009), (2010, 2019), (2020, last)]
    period_text = ", ".join(
        f"{start}-{end}: {cambodia.loc[cambodia['year'].between(start, end), 'value'].mean():.2f}%"
        for start, end in periods
    )
    contractions = cambodia.loc[cambodia["value"] < 0, "year"].tolist()
    others = ", ".join(f"{c} {v:.2f}%" for c, v in averages.items() if c != "Cambodia")
    rank_text = "highest" if rank == 1 else f"{ordinal(rank)} highest"
    return (f"**Growth.** Cambodia's real GDP growth averaged **{averages['Cambodia']:.2f}%** a year "
            f"in {first}-{last}, the {rank_text} of the four ({others}). Average by period: "
            f"{period_text}. Years with negative growth: "
            f"{year_ranges(contractions) if contractions else 'none'}.")


def income_insight(df: pd.DataFrame, metadata: dict) -> str:
    unit = metadata["indicators"][GDP_PER_CAPITA]["unit"]
    gdp = wide_table(df, GDP_PER_CAPITA)
    cambodia = gdp["Cambodia"].dropna()
    first_year, latest_year = cambodia.index[0], cambodia.index[-1]
    latest = gdp.loc[latest_year].dropna().sort_values(ascending=False)
    rank = list(latest.index).index("Cambodia") + 1
    ratios = ", ".join(f"{c} {latest[c] / latest['Cambodia']:.2f}x" for c in latest.index if c != "Cambodia")
    return (f"**Income.** Cambodia's GDP per capita rose from **{format_value(cambodia.iloc[0], unit, True)}** "
            f"({first_year}) to **{format_value(cambodia.iloc[-1], unit, True)}** ({latest_year}), "
            f"{cambodia.iloc[-1] / cambodia.iloc[0]:.1f} times higher in current US\\$ (not adjusted for "
            f"inflation). In {latest_year} it ranked {ordinal(rank)} of {len(latest)}. Other countries "
            f"relative to Cambodia: {ratios}.")


def internet_insight(df: pd.DataFrame) -> str:
    internet = wide_table(df, INTERNET_USERS)
    cambodia = internet["Cambodia"].dropna()
    milestones = []
    for country in internet.columns:
        series = internet[country].dropna()
        reached = series.index[series >= 50]
        milestones.append(f"{country} {reached.min()}" if len(reached)
                          else f"{country} not reached by {series.index.max()} (latest data)")
    return (f"**Internet adoption.** Internet users in Cambodia rose from **{cambodia.iloc[0]:.2f}%** "
            f"({cambodia.index[0]}) to **{cambodia.iloc[-1]:.2f}%** ({cambodia.index[-1]}). First year "
            f"at or above 50% of the population: {'; '.join(milestones)}.")


def servers_insight(df: pd.DataFrame) -> str:
    servers = wide_table(df, SECURE_SERVERS)
    cambodia = servers["Cambodia"].dropna()
    latest_year = cambodia.index[-1]
    latest = servers.loc[latest_year].dropna()
    leader = latest.idxmax()
    text = (f"**Secure Internet servers.** Cambodia rose from **{cambodia.iloc[0]:,.2f}** per million "
            f"people ({cambodia.index[0]}) to **{cambodia.iloc[-1]:,.1f}** ({latest_year}).")
    if leader != "Cambodia":
        text += (f" {leader}'s {latest_year} value ({latest[leader]:,.1f}) is "
                 f"{latest[leader] / latest['Cambodia']:.1f} times Cambodia's.")
    return text + (" The indicator counts TLS/SSL certificates by hosting country, so it shows where "
                   "secure websites are hosted, not how well a country is protected.")


def correlation_insight(df: pd.DataFrame) -> str:
    pairs = internet_income_pairs(df, COUNTRY_ORDER)
    pearson = pairs[INTERNET_USERS].corr(pairs[GDP_PER_CAPITA])
    per_country = pairs.groupby("country").apply(lambda g: g[INTERNET_USERS].corr(g[GDP_PER_CAPITA]))
    direction = "positive" if pearson > 0 else "negative"
    return (f"**Internet use and income.** Across {len(pairs)} country-years, Pearson r = "
            f"**{pearson:.2f}** ({direction}). Within single countries r ranges from "
            f"{per_country.min():.2f} ({per_country.idxmin()}) to {per_country.max():.2f} "
            f"({per_country.idxmax()}). Both indicators rise over time, so this is an association, "
            f"not evidence that one causes the other.")


def show_insights(df: pd.DataFrame, metadata: dict) -> None:
    st.header("Key insights")
    st.caption("Calculated from the dataset each time the page loads, for all four countries "
               "(not affected by the country filter).")
    for insight in [growth_insight(df), income_insight(df, metadata), internet_insight(df),
                    servers_insight(df), correlation_insight(df)]:
        st.markdown(f"- {insight}")


# ---------------------------------------------------------------------------
# Section F: data source and methodology
# ---------------------------------------------------------------------------

def show_methodology(df: pd.DataFrame, metadata: dict) -> None:
    st.header("Data source and methodology")
    first_year, last_year = metadata["dataset_year_range"]
    last_updated = metadata["indicators"][GDP_PER_CAPITA]["database_last_updated"]
    st.markdown(
        f"- **Source:** World Bank, [World Development Indicators]({metadata['source_url']}), "
        f"licensed {metadata['license']}. Retrieved through the World Bank API ({metadata['api']}).\n"
        f"- **Retrieved:** {metadata['retrieved_at_utc'].replace('T', ' ').replace('+00:00', ' UTC')} "
        f"(World Bank database last updated {last_updated}).\n"
        f"- **Coverage:** {', '.join(COUNTRY_ORDER)}; {first_year}-{last_year}; "
        f"{len(metadata['indicators'])} indicators; {metadata['rows']} country-year-indicator rows.\n"
        f"- **Missing observations** are shown as gaps and never estimated or replaced with zero. "
        f"{int(df['value'].isna().sum())} of {len(df)} rows have no published value."
    )

    st.subheader("Latest year with data")
    latest = (df.dropna(subset=["value"]).groupby(["indicator", "country"])["year"].max()
              .unstack().reindex(columns=COUNTRY_ORDER).rename_axis(index="Indicator", columns=None))
    st.dataframe(latest)

    st.subheader("Limitations")
    st.markdown(
        "- **Secure Internet servers** is a digital infrastructure indicator (publicly-trusted "
        "TLS/SSL certificates by hosting country). It is **not** a comprehensive measure of "
        "cybersecurity readiness or national cyber resilience.\n"
        "- **GDP per capita in current US\\$** is affected by inflation and exchange rates; "
        "use GDP growth (constant prices) for real growth.\n"
        "- The most recent values may be revised in later World Bank releases.\n"
        "- Gaps include Vietnam remittances after 2004, Myanmar inflation after 2019 and "
        "Internet users after 2020, and Secure Internet servers before 2010.\n"
        "- Correlations describe association only and are based on a small sample."
    )

    with st.expander("Indicator definitions"):
        for code, info in metadata["indicators"].items():
            st.markdown(f"**{info['world_bank_name']}** (`{code}`): {info['definition']}  \n"
                        f"*Source: {info['source_organization']}*")


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Cambodia Digital Economy Dashboard", page_icon="📊", layout="wide")
    df, metadata = load_data()

    with st.sidebar:
        st.header("Filters")
        countries = st.multiselect("Countries", COUNTRY_ORDER, default=COUNTRY_ORDER,
                                   help="Applies to the trend chart and the scatter plot.")
        st.caption(f"Data retrieved {metadata['retrieved_at_utc'][:10]} from the World Bank.")

    show_introduction(df, metadata)
    st.divider()
    show_kpis(df, metadata)
    st.divider()
    if countries:
        show_trends(df, metadata, countries)
        st.divider()
        show_scatter(df, countries)
    else:
        st.info("Select at least one country in the sidebar to see the charts.")
    st.divider()
    show_insights(df, metadata)
    st.divider()
    show_methodology(df, metadata)


main()
