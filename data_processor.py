import pandas as pd
from sqlalchemy import create_engine

from db_config import DB_CONFIG, get_sqlalchemy_url


CONFIG = {
    "TEMP_MIN": 18.0,
    "TEMP_MAX": 28.0,
    "OUT_OF_RANGE_THRESHOLD_MINUTES": 3,
    "DEFAULT_STATION_KEY": None,
}


def _get_engine():
    return create_engine(get_sqlalchemy_url())


def load_data() -> dict:
    """Load temperature, cover history, and cover status from PostgreSQL.

    Returns dict with keys "temperature", "cover_history", "cover_status",
    each a pd.DataFrame with columns matching the original Excel canonical names.
    """
    engine = _get_engine()

    temperature = pd.read_sql(
        "SELECT temperature AS \"Temperature\", "
        "       timestamp AS \"Timestamp\", "
        "       station_key AS \"Station Key\" "
        "FROM temperature ORDER BY timestamp",
        engine,
    )

    cover_history = pd.read_sql(
        "SELECT cover_status_id AS \"Cover status id\", "
        "       timestamp AS \"Timestamp\", "
        "       station_key AS \"Station Key\" "
        "FROM cover_history ORDER BY timestamp",
        engine,
    )

    cover_status = pd.read_sql(
        "SELECT cover_status_id AS \"Cover status id\", "
        "       cover_status_description AS \"Cover status description\" "
        "FROM cover_status",
        engine,
    )

    engine.dispose()

    return {
        "temperature": temperature if not temperature.empty else None,
        "cover_history": cover_history if not cover_history.empty else None,
        "cover_status": cover_status if not cover_status.empty else None,
    }


def clean_temperature_data(df: pd.DataFrame) -> pd.DataFrame:
    required = {"Temperature", "Timestamp", "Station Key"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[WARNING] Temperature data missing columns: {missing}")
        return df

    df = df.copy()

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Temperature"] = pd.to_numeric(df["Temperature"], errors="coerce")
    df = df.dropna(subset=["Temperature", "Timestamp"])
    df = df.drop_duplicates(subset="Timestamp", keep="first")
    df = df.sort_values("Timestamp").reset_index(drop=True)

    unique_stations = df["Station Key"].nunique()
    if unique_stations > 1:
        print(f"[WARNING] Multiple stations found: {unique_stations}")

    default_key = CONFIG.get("DEFAULT_STATION_KEY")
    if default_key is not None:
        df = df[df["Station Key"] == default_key]
    elif unique_stations > 1:
        first_station = df["Station Key"].mode().iloc[0]
        df = df[df["Station Key"] == first_station]
        print(f"[INFO] Filtered to station: {first_station}")

    return df.reset_index(drop=True)


def clean_cover_data(df: pd.DataFrame, status_ref: pd.DataFrame) -> pd.DataFrame:
    required = ["Cover status id", "Timestamp", "Station Key"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[WARNING] Cover history missing columns: {missing}")
        return df

    df = df.copy()

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Cover status id"] = pd.to_numeric(df["Cover status id"], errors="coerce")
    df = df.dropna(subset=["Timestamp", "Cover status id"])

    valid_ids = {0, 1}
    actual_ids = set(df["Cover status id"].dropna().unique())
    invalid = actual_ids - valid_ids
    if invalid:
        print(f"[WARNING] Unexpected cover status IDs found: {sorted(invalid)}")

    if status_ref is not None and len(status_ref) > 0:
        if "Cover status id" in status_ref.columns and "Cover status description" in status_ref.columns:
            status_map = dict(zip(
                status_ref["Cover status id"],
                status_ref["Cover status description"],
            ))
            df["Cover status description"] = df["Cover status id"].map(status_map)
        else:
            df["Cover status description"] = df["Cover status id"].map({0: "Closed", 1: "Opened"})
    else:
        df["Cover status description"] = df["Cover status id"].map({0: "Closed", 1: "Opened"})

    df = df.sort_values("Timestamp").reset_index(drop=True)
    return df


def validate_data(temp_df: pd.DataFrame, cover_df: pd.DataFrame) -> list[str]:
    warnings: list[str] = []

    if temp_df is None or (isinstance(temp_df, pd.DataFrame) and temp_df.empty):
        warnings.append("Temperature data is empty or None")
    if cover_df is None or (isinstance(cover_df, pd.DataFrame) and cover_df.empty):
        warnings.append("Cover history data is empty or None")

    required_temp = {"Temperature", "Timestamp", "Station Key"}
    if isinstance(temp_df, pd.DataFrame) and not temp_df.empty:
        for col in required_temp:
            if col not in temp_df.columns:
                warnings.append(f"Temperature data missing column: {col}")

    required_cover = {"Cover status id", "Timestamp", "Station Key"}
    if isinstance(cover_df, pd.DataFrame) and not cover_df.empty:
        for col in required_cover:
            if col not in cover_df.columns:
                warnings.append(f"Cover history missing column: {col}")

    if (
        isinstance(temp_df, pd.DataFrame)
        and not temp_df.empty
        and isinstance(cover_df, pd.DataFrame)
        and not cover_df.empty
        and "Timestamp" in temp_df.columns
        and "Timestamp" in cover_df.columns
    ):
        temp_min = temp_df["Timestamp"].min()
        temp_max = temp_df["Timestamp"].max()
        cover_min = cover_df["Timestamp"].min()
        cover_max = cover_df["Timestamp"].max()
        if temp_max < cover_min or cover_max < temp_min:
            warnings.append(
                f"No date range overlap — temp [{temp_min} to {temp_max}], "
                f"cover [{cover_min} to {cover_max}]"
            )

    if (
        isinstance(temp_df, pd.DataFrame)
        and not temp_df.empty
        and "Temperature" in temp_df.columns
        and temp_df["Temperature"].isna().all()
    ):
        warnings.append("All temperature values are NaN")

    return warnings


def compute_hourly_averages(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 60:
        return df.copy()

    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"])
    df = df.set_index("Timestamp")

    hourly = df.resample("1h").agg({"Temperature": "mean", "Station Key": "first"})
    hourly = hourly.dropna(subset=["Temperature"]).reset_index()
    hourly["Temperature"] = hourly["Temperature"].round(1)
    return hourly


def detect_out_of_range(df: pd.DataFrame, config: dict) -> list[dict]:
    if df is None or df.empty:
        return []

    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Temperature"] = pd.to_numeric(df["Temperature"], errors="coerce")
    df = df.dropna(subset=["Timestamp", "Temperature"])

    temp_min = config["TEMP_MIN"]
    temp_max = config["TEMP_MAX"]

    df["out_of_range"] = (df["Temperature"] < temp_min) | (df["Temperature"] > temp_max)

    if not df["out_of_range"].any():
        return []

    df["group"] = (df["out_of_range"] != df["out_of_range"].shift()).cumsum()

    threshold = config.get("OUT_OF_RANGE_THRESHOLD_MINUTES", 3)
    events: list[dict] = []

    for _gid, group in df[df["out_of_range"]].groupby("group"):
        if len(group) < threshold:
            continue

        start_time = group["Timestamp"].iloc[0]
        end_time = group["Timestamp"].iloc[-1]
        duration = len(group)

        if group["Temperature"].iloc[0] > temp_max:
            direction = "above"
            max_deviation = float((group["Temperature"] - temp_max).max())
            extreme_label = "max"
            extreme_val = float(group["Temperature"].max())
        else:
            direction = "below"
            max_deviation = float((temp_min - group["Temperature"]).max())
            extreme_label = "min"
            extreme_val = float(group["Temperature"].min())

        events.append({
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": duration,
            "max_deviation": max_deviation,
            "avg_temperature": float(round(group["Temperature"].mean(), 1)),
            "direction": direction,
            "hour": start_time.floor("h"),
            "extreme_label": extreme_label,
            "extreme_value": round(extreme_val, 1),
        })

    events.sort(key=lambda e: e["start_time"])
    return events


def count_out_of_range(events: list[dict]) -> int:
    return len(events)


def format_out_of_range_annotation(event: dict) -> str:
    direction = event["direction"]
    duration = event["duration_minutes"]
    extreme_label = event.get("extreme_label", "max" if direction == "above" else "min")
    extreme_value = event.get("extreme_value", 0)

    if direction == "above":
        boundary = CONFIG.get("TEMP_MAX", 28.0)
        return f"Above {boundary}°C for {duration} min (max: {extreme_value}°C)"
    else:
        boundary = CONFIG.get("TEMP_MIN", 18.0)
        return f"Below {boundary}°C for {duration} min (min: {extreme_value}°C)"


def process_cover_events(cover_df: pd.DataFrame, temp_df: pd.DataFrame) -> list[dict]:
    if cover_df is None or cover_df.empty:
        return []

    df = cover_df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Cover status id"] = pd.to_numeric(df["Cover status id"], errors="coerce")
    df = df.dropna(subset=["Timestamp", "Cover status id"]).sort_values("Timestamp").reset_index(drop=True)

    events: list[dict] = []
    i = 0
    while i < len(df):
        row = df.iloc[i]
        if row["Cover status id"] == 1:
            open_time = row["Timestamp"]
            close_time = None
            for j in range(i + 1, len(df)):
                if df.iloc[j]["Cover status id"] == 0:
                    close_time = df.iloc[j]["Timestamp"]
                    i = j + 1
                    break
            else:
                i += 1

            if close_time is not None:
                dur = (close_time - open_time).total_seconds() / 60.0
            else:
                dur = None

            events.append({
                "open_time": open_time,
                "close_time": close_time,
                "duration_minutes": dur,
                "status": "opened",
            })
        else:
            i += 1

    events.sort(key=lambda e: e["open_time"])
    return events


def align_cover_with_temperature(
    cover_events: list[dict],
    temp_hourly: pd.DataFrame,
) -> list[dict]:
    if not cover_events:
        return []

    if temp_hourly is None or temp_hourly.empty:
        for ev in cover_events:
            ev["overlap_hours"] = []
        return cover_events

    hourly_ts = pd.to_datetime(temp_hourly["Timestamp"], errors="coerce").dropna().sort_values()

    for ev in cover_events:
        open_t = ev["open_time"]
        close_t = ev["close_time"] if ev["close_time"] is not None else hourly_ts.max()
        overlaps = hourly_ts[(hourly_ts >= open_t.floor("h")) & (hourly_ts <= close_t.ceil("h"))]
        ev["overlap_hours"] = overlaps.tolist()

    return cover_events
