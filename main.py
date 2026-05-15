import plotly.graph_objects as go
from flask import Flask, render_template, make_response

from data_processor import (
    CONFIG,
    load_data,
    clean_temperature_data,
    clean_cover_data,
    compute_hourly_averages,
    detect_out_of_range,
    count_out_of_range,
    process_cover_events,
    align_cover_with_temperature,
)

app = Flask(__name__, template_folder="templates")

_load_error = None
_hourly = None
_out_of_range = None
_cover_aligned = None
_station_count = 0
_temp_cleaned = None  # kept so we can pass raw events to template

try:
    _raw = load_data()
    _temp_cleaned = clean_temperature_data(_raw["temperature"])
    _cover = clean_cover_data(_raw["cover_history"], _raw["cover_status"])
    _hourly = compute_hourly_averages(_temp_cleaned)
    _out_of_range = detect_out_of_range(_temp_cleaned, CONFIG)
    _cover_events = process_cover_events(_cover, _temp_cleaned)
    _cover_aligned = align_cover_with_temperature(_cover_events, _hourly)
    _station_count = (
        _temp_cleaned["Station Key"].nunique()
        if _temp_cleaned is not None and len(_temp_cleaned) > 0
        else 0
    )
    print(
        f"[INFO] Data loaded: {len(_hourly)} hourly points, "
        f"{count_out_of_range(_out_of_range)} out-of-range events, "
        f"{len(_cover_aligned)} cover events"
    )
except Exception as e:
    _load_error = f"Error loading data: {e}"
    print(f"[ERROR] {_load_error}")


# ---------------------------------------------------------------------------
# Route: main dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if _load_error:
        return render_template(
            "index.html",
            chart_html="",
            out_of_range_count=0,
            station_count=0,
            error_message=_load_error,
            out_of_range_events=[],
        )

    fig = build_chart(_hourly, _out_of_range, _cover_aligned, CONFIG)
    chart_html = render_chart_html(fig)

    # Build table rows for the out-of-range detail panel
    table_events = _build_table_events(_out_of_range, _temp_cleaned, CONFIG)

    return render_template(
        "index.html",
        chart_html=chart_html,
        out_of_range_count=count_out_of_range(_out_of_range),
        station_count=_station_count,
        error_message=None,
        out_of_range_events=table_events,
    )


# ---------------------------------------------------------------------------
# Route: export out-of-range events to Excel
# ---------------------------------------------------------------------------

@app.route("/export-excel")
def export_excel():
    """Download the out-of-range events table as an Excel file."""
    import io
    import pandas as pd

    table_events = _build_table_events(_out_of_range, _temp_cleaned, CONFIG)

    if not table_events:
        # Return empty file with headers only
        df = pd.DataFrame(columns=[
            "Station", "Start Time", "End Time",
            "Duration (min)", "Direction",
            "Avg Temp (°C)", "Extreme Temp (°C)", "Limit Exceeded (°C)"
        ])
    else:
        df = pd.DataFrame(table_events)
        df = df.rename(columns={
            "station":          "Station",
            "start_time":       "Start Time",
            "end_time":         "End Time",
            "duration_minutes": "Duration (min)",
            "direction":        "Direction",
            "avg_temperature":  "Avg Temp (°C)",
            "extreme_value":    "Extreme Temp (°C)",
            "limit":            "Limit Exceeded (°C)",
        })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Out-of-Range Events")

    output.seek(0)
    response = make_response(output.read())
    response.headers["Content-Disposition"] = "attachment; filename=out_of_range_events.xlsx"
    response.headers["Content-Type"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return response


# ---------------------------------------------------------------------------
# Chart builder  (Person 2 responsibility)
# ---------------------------------------------------------------------------

def build_chart(
    hourly_df,
    out_of_range_events: list[dict],
    cover_events: list[dict],
    config: dict,
) -> go.Figure:
    """Build the Plotly interactive temperature timeline chart.

    Layers (in order):
      1. Valid-range band        – subtle grey/white fill (was green, now neutral)
      2. Temperature line        – blue
      3. Upper & lower limit lines
      4. Cover-open regions      – orange semi-transparent vrects
      5. Out-of-range star markers with station + duration annotation
    """

    fig = go.Figure()

    # ------------------------------------------------------------------
    # 1. Valid-range band  (neutral grey, not green)
    # ------------------------------------------------------------------
    fig.add_hrect(
        y0=config["TEMP_MIN"],
        y1=config["TEMP_MAX"],
        fillcolor="rgba(180, 180, 180, 0.10)",   # subtle neutral grey
        line_width=0,
        annotation_text=f"Valid range: {config['TEMP_MIN']}–{config['TEMP_MAX']}°C",
        annotation_position="top left",
        annotation_font_size=10,
        annotation_font_color="#888888",
    )

    # ------------------------------------------------------------------
    # 2. Temperature line
    # ------------------------------------------------------------------
    if hourly_df is not None and len(hourly_df) > 0:
        fig.add_trace(
            go.Scatter(
                x=hourly_df["Timestamp"],
                y=hourly_df["Temperature"],
                mode="lines",
                name="Temperature (°C)",
                line=dict(color="#2196F3", width=2),
                hovertemplate="Time: %{x}<br>Temperature: %{y:.1f}°C<extra></extra>",
            )
        )

    # ------------------------------------------------------------------
    # 3. Limit lines
    # ------------------------------------------------------------------
    fig.add_hline(
        y=config["TEMP_MAX"],
        line_dash="dash",
        line_color="red",
        annotation_text=f"Upper limit: {config['TEMP_MAX']}°C",
        annotation_position="top right",
        annotation_font_color="red",
    )
    fig.add_hline(
        y=config["TEMP_MIN"],
        line_dash="dash",
        line_color="blue",
        annotation_text=f"Lower limit: {config['TEMP_MIN']}°C",
        annotation_position="bottom right",
        annotation_font_color="blue",
    )

    # ------------------------------------------------------------------
    # 4. Cover-open regions  (orange vrects)
    # ------------------------------------------------------------------
    if cover_events:
        for event in cover_events:
            close = (
                event["close_time"]
                if event.get("close_time") is not None
                else event["open_time"]
            )
            fig.add_vrect(
                x0=event["open_time"],
                x1=close,
                fillcolor="rgba(255, 165, 0, 0.15)",
                line_width=0,
            )

    # ------------------------------------------------------------------
    # 5. Out-of-range star markers
    #    – only shown where temperature is actually out of range
    #    – hover shows: station, duration, avg temp
    # ------------------------------------------------------------------
    if out_of_range_events:
        star_x, star_y, star_text, star_hover = [], [], [], []

        for e in out_of_range_events:
            # Find the actual temperature value at the event's hour
            y_val = e["avg_temperature"]
            if hourly_df is not None and len(hourly_df) > 0:
                mask = hourly_df["Timestamp"] == e["hour"]
                if mask.any():
                    y_val = float(hourly_df.loc[mask, "Temperature"].iloc[0])

            station = e.get("station_key", "N/A")
            direction_label = "Above" if e["direction"] == "above" else "Below"
            limit_val = config["TEMP_MAX"] if e["direction"] == "above" else config["TEMP_MIN"]
            duration = e["duration_minutes"]
            extreme = e.get("extreme_value", round(y_val, 1))

            star_x.append(e["hour"])
            star_y.append(y_val)

            # Short annotation beside the star
            star_text.append(
                f"{direction_label} {limit_val}°C | {duration} min"
            )

            # Rich hover tooltip
            star_hover.append(
                f"<b>⚠ Out of Range</b><br>"
                f"Station: {station}<br>"
                f"Direction: {direction_label} {limit_val}°C<br>"
                f"Duration: {duration} min<br>"
                f"Avg temp: {e['avg_temperature']:.1f}°C<br>"
                f"Extreme: {extreme}°C"
            )

        fig.add_trace(
            go.Scatter(
                x=star_x,
                y=star_y,
                mode="markers",
                name="Out-of-Range Events",
                marker=dict(symbol="star", size=15, color="red"),
                hovertext=star_hover,
                hoverinfo="text",
            )
        )

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    fig.update_layout(
        title="SPM Temperature Timeline",
        xaxis=dict(
            title="Time",
            rangeselector=dict(
                buttons=[
                    dict(count=1,  label="1h", step="hour", stepmode="backward"),
                    dict(count=1,  label="1d", step="day",  stepmode="backward"),
                    dict(count=7,  label="1w", step="day",  stepmode="backward"),
                    dict(step="all", label="All"),
                ],
                bgcolor="#f0f0f0",
                activecolor="#d0d0d0",
            ),
            rangeslider=dict(visible=True),
        ),
        yaxis=dict(
            title="Temperature (°C)",
            range=[config["TEMP_MIN"] - 2, config["TEMP_MAX"] + 2],
        ),
        hovermode="x unified",
        height=600,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=50, r=30, t=80, b=80),
    )

    return fig


def render_chart_html(fig: go.Figure) -> str:
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


# ---------------------------------------------------------------------------
# Helper: build table rows for the out-of-range detail panel
# ---------------------------------------------------------------------------

def _build_table_events(
    out_of_range_events: list[dict] | None,
    temp_df,
    config: dict,
) -> list[dict]:
    """Convert raw out-of-range event dicts into flat rows for the HTML table
    and for the Excel export.

    Each row contains:
        station, start_time, end_time, duration_minutes,
        direction, avg_temperature, extreme_value, limit
    """
    if not out_of_range_events:
        return []

    rows = []
    for e in out_of_range_events:
        direction = e["direction"]
        limit = config["TEMP_MAX"] if direction == "above" else config["TEMP_MIN"]
        direction_label = f"Above {limit}°C" if direction == "above" else f"Below {limit}°C"

        # Try to resolve station key from the raw temperature dataframe
        station = e.get("station_key", "N/A")
        if station == "N/A" and temp_df is not None and not temp_df.empty:
            mask = (
                (temp_df["Timestamp"] >= e["start_time"]) &
                (temp_df["Timestamp"] <= e["end_time"])
            )
            if mask.any():
                station = temp_df.loc[mask, "Station Key"].iloc[0]

        rows.append({
            "station":          station,
            "start_time":       e["start_time"].strftime("%Y-%m-%d %H:%M"),
            "end_time":         e["end_time"].strftime("%Y-%m-%d %H:%M"),
            "duration_minutes": e["duration_minutes"],
            "direction":        direction_label,
            "avg_temperature":  round(e["avg_temperature"], 1),
            "extreme_value":    e.get("extreme_value", round(e["avg_temperature"], 1)),
            "limit":            limit,
        })

    return rows


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)