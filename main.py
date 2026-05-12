import plotly.graph_objects as go
from flask import Flask, render_template

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

try:
    _raw = load_data()
    _temp = clean_temperature_data(_raw["temperature"])
    _cover = clean_cover_data(_raw["cover_history"], _raw["cover_status"])
    _hourly = compute_hourly_averages(_temp)
    _out_of_range = detect_out_of_range(_temp, CONFIG)
    _cover_events = process_cover_events(_cover, _temp)
    _cover_aligned = align_cover_with_temperature(_cover_events, _hourly)
    _station_count = _temp["Station Key"].nunique() if _temp is not None and len(_temp) > 0 else 0
    print(f"[INFO] Data loaded: {len(_hourly)} hourly points, {count_out_of_range(_out_of_range)} out-of-range events, {len(_cover_aligned)} cover events")
except Exception as e:
    _load_error = f"Error loading data: {e}"
    print(f"[ERROR] {_load_error}")


@app.route("/")
def index():
    if _load_error:
        return render_template("index.html",
            chart_html="",
            out_of_range_count=0,
            station_count=0,
            error_message=_load_error,
        )
    fig = build_chart(_hourly, _out_of_range, _cover_aligned, CONFIG)
    chart_html = render_chart_html(fig)
    return render_template("index.html",
        chart_html=chart_html,
        out_of_range_count=count_out_of_range(_out_of_range),
        station_count=_station_count,
        error_message=None,
    )


def build_chart(hourly_df, out_of_range_events: list[dict], cover_events: list[dict], config: dict) -> go.Figure:
    fig = go.Figure()

    fig.add_hrect(
        y0=config["TEMP_MIN"],
        y1=config["TEMP_MAX"],
        fillcolor="rgba(0, 200, 0, 0.08)",
        line_width=0,
        annotation_text=f"Range: {config['TEMP_MIN']}–{config['TEMP_MAX']}°C",
        annotation_position="top left",
        annotation_font_size=10,
        annotation_font_color="green",
    )

    if hourly_df is not None and len(hourly_df) > 0:
        fig.add_trace(go.Scatter(
            x=hourly_df["Timestamp"],
            y=hourly_df["Temperature"],
            mode="lines",
            name="Temperature (°C)",
            line=dict(color="#2196F3", width=2),
            hovertemplate="Time: %{x}<br>Temperature: %{y:.1f}°C<extra></extra>",
        ))

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

    if out_of_range_events:
        star_x = [e["hour"] for e in out_of_range_events]
        star_y = []
        star_text = []
        for e in out_of_range_events:
            if hourly_df is not None and len(hourly_df) > 0 and "Timestamp" in hourly_df.columns and "Temperature" in hourly_df.columns:
                mask = hourly_df["Timestamp"] == e["hour"]
                if mask.any():
                    star_y.append(hourly_df.loc[mask, "Temperature"].iloc[0])
                else:
                    star_y.append(e["avg_temperature"])
            else:
                star_y.append(e["avg_temperature"])
            star_text.append(f"{'⚠ Above' if e['direction'] == 'above' else '⚠ Below'} {config['TEMP_MAX'] if e['direction'] == 'above' else config['TEMP_MIN']}°C for {e['duration_minutes']}min (avg: {e['avg_temperature']:.1f}°C)")

        fig.add_trace(go.Scatter(
            x=star_x,
            y=star_y,
            mode="markers+text",
            name="Out-of-Range Events",
            marker=dict(symbol="star", size=15, color="red"),
            text=star_text,
            textposition="top center",
            textfont=dict(size=9, color="red"),
            hoverinfo="text",
        ))

    if cover_events:
        for event in cover_events:
            close = event["close_time"] if event.get("close_time") is not None else event["open_time"]
            fig.add_vrect(
                x0=event["open_time"],
                x1=close,
                fillcolor="rgba(255, 165, 0, 0.15)",
                line_width=0,
            )

    fig.update_layout(
        title="SPM Temperature Timeline",
        xaxis=dict(
            title="Time",
            rangeselector=dict(
                buttons=[
                    dict(count=1, label="1h", step="hour", stepmode="backward"),
                    dict(count=1, label="1d", step="day", stepmode="backward"),
                    dict(count=7, label="1w", step="day", stepmode="backward"),
                    dict(step="all"),
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
