from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests


# ============================================================
# PATHS / CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LIVE_URL = (
    "https://data.addinsight.com/ACT/"
    "links_prop_stats_geo.json"
)

PRODUCTION_DIR = PROJECT_ROOT / "data" / "production"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_DIR = (
    PROJECT_ROOT / "deployment" / "models"
)

NETWORK_PATH = (
    PROJECT_ROOT / "deployment" / "current_network.parquet"
)

HISTORY_PATH = (
    PRODUCTION_DIR / "live_history.parquet"
)

FORECAST_PARQUET_PATH = (
    PRODUCTION_DIR / "live_forecasts.parquet"
)

JSON_PATH = (
    PROJECT_ROOT
    / "docs"
    / "data"
    / "latest_forecasts.json"
)

HORIZONS = [15, 30, 60, 120]

LAG_MINUTES = [5, 15, 30, 60]

LAG_TOLERANCE_SECONDS = 150

HISTORY_RETENTION_MINUTES = 90


M1_FEATURES = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "is_weekend",

    "length_m",
    "minnumberoflanes",
    "isfreeway",

    "origin_x_m",
    "origin_y_m",
    "destination_x_m",
    "destination_y_m",
    "midpoint_x_m",
    "midpoint_y_m",
    "bearing_sin",
    "bearing_cos",
    "geography_resolved",

    "rd_now",
    "rd_lag_5m",
    "rd_lag_15m",
    "rd_lag_30m",
    "rd_lag_60m",
    "rd_change_5m",
    "rd_change_15m",
    "rd_change_30m",
]


HISTORY_COLUMNS = [
    "timestamp",
    "originsiteid",
    "destsiteid",
    "tt",
    "mintt",
    "relative_delay",
]


# ============================================================
# HELPERS
# ============================================================

def iso_or_none(value):
    if pd.isna(value):
        return None

    return pd.Timestamp(value).isoformat()


def float_or_none(value):
    if pd.isna(value):
        return None

    value = float(value)

    if not np.isfinite(value):
        return None

    return value


def int_or_none(value):
    if pd.isna(value):
        return None

    return int(value)


# ============================================================
# 1. FETCH LIVE ADDINSIGHT SNAPSHOT
# ============================================================

def fetch_live_snapshot():

    response = requests.get(
        LIVE_URL,
        timeout=30,
    )

    response.raise_for_status()

    payload = response.json()

    features = payload["features"]

    rows = []

    for feature in features:

        props = feature.get(
            "properties",
            {},
        )

        geometry = feature.get("geometry")

        rows.append(
            {
                "source_id":
                    props.get("Id"),

                "source_name":
                    props.get("Name"),

                "originsiteid":
                    props.get("OriginSiteId"),

                "destsiteid":
                    props.get("DestSiteId"),

                "timestamp":
                    props.get("IntervalStart"),

                "tt":
                    props.get("TT"),

                "mintt":
                    props.get("MinTT"),

                "coordinates":
                    (
                        geometry.get("coordinates")
                        if geometry
                        else None
                    ),
            }
        )

    live = pd.DataFrame(rows)

    live["timestamp"] = pd.to_datetime(
        live["timestamp"],
        utc=True,
        errors="coerce",
    )

    live["originsiteid"] = pd.to_numeric(
        live["originsiteid"],
        errors="coerce",
    ).astype("Int64")

    live["destsiteid"] = pd.to_numeric(
        live["destsiteid"],
        errors="coerce",
    ).astype("Int64")

    live["tt"] = pd.to_numeric(
        live["tt"],
        errors="coerce",
    )

    live["mintt"] = pd.to_numeric(
        live["mintt"],
        errors="coerce",
    )

    live["relative_delay"] = np.where(
        live["mintt"] > 0,
        (
            live["tt"]
            - live["mintt"]
        )
        / live["mintt"],
        np.nan,
    )

    # One coherent live snapshot is expected.
    valid_timestamps = (
        live["timestamp"]
        .dropna()
        .unique()
    )

    if len(valid_timestamps) != 1:
        raise RuntimeError(
            "Expected one AddInsight timestamp, "
            f"found {len(valid_timestamps)}."
        )

    duplicate_links = live.duplicated(
        [
            "originsiteid",
            "destsiteid",
        ]
    ).sum()

    if duplicate_links:
        raise RuntimeError(
            "Duplicate directed links in live "
            f"snapshot: {duplicate_links}"
        )

    return live


# ============================================================
# 2. UPDATE PERSISTENT ROLLING HISTORY
# ============================================================

def update_history(live):

    PRODUCTION_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    new_snapshot = live[
        HISTORY_COLUMNS
    ].copy()

    if HISTORY_PATH.exists():

        history = pd.read_parquet(
            HISTORY_PATH
        )

        history["timestamp"] = pd.to_datetime(
            history["timestamp"],
            utc=True,
        )

        history = pd.concat(
            [
                history,
                new_snapshot,
            ],
            ignore_index=True,
        )

    else:

        history = new_snapshot.copy()

    # Idempotent if Action sees same API interval twice.
    history = (
        history
        .sort_values(
            [
                "timestamp",
                "originsiteid",
                "destsiteid",
            ]
        )
        .drop_duplicates(
            subset=[
                "timestamp",
                "originsiteid",
                "destsiteid",
            ],
            keep="last",
        )
    )

    latest_timestamp = (
        history["timestamp"].max()
    )

    cutoff = (
        latest_timestamp
        - pd.Timedelta(
            minutes=HISTORY_RETENTION_MINUTES
        )
    )

    history = (
        history[
            history["timestamp"] >= cutoff
        ]
        .copy()
        .sort_values(
            [
                "originsiteid",
                "destsiteid",
                "timestamp",
            ]
        )
        .reset_index(drop=True)
    )

    history.to_parquet(
        HISTORY_PATH,
        index=False,
    )

    return history


# ============================================================
# 3. ELAPSED-TIME LAG MATCHING
# ============================================================

def attach_lag(
    current,
    history,
    lag_minutes,
):

    target = current[
        [
            "originsiteid",
            "destsiteid",
            "timestamp",
        ]
    ].copy()

    target["wanted_timestamp"] = (
        target["timestamp"]
        - pd.Timedelta(
            minutes=lag_minutes
        )
    )

    candidates = history[
        [
            "originsiteid",
            "destsiteid",
            "timestamp",
            "relative_delay",
        ]
    ].copy()

    merged = target.merge(
        candidates,
        on=[
            "originsiteid",
            "destsiteid",
        ],
        how="left",
        suffixes=(
            "_origin",
            "_candidate",
        ),
    )

    merged["distance_seconds"] = (
        merged["timestamp_candidate"]
        - merged["wanted_timestamp"]
    ).abs().dt.total_seconds()

    merged = merged[
        merged["distance_seconds"]
        <= LAG_TOLERANCE_SECONDS
    ].copy()

    lag_name = (
        f"rd_lag_{lag_minutes}m"
    )

    if merged.empty:

        result = target[
            [
                "originsiteid",
                "destsiteid",
            ]
        ].copy()

        result[lag_name] = np.nan

        return result

    merged = (
        merged
        .sort_values(
            [
                "originsiteid",
                "destsiteid",
                "distance_seconds",
                "timestamp_candidate",
            ]
        )
        .drop_duplicates(
            [
                "originsiteid",
                "destsiteid",
            ],
            keep="first",
        )
    )

    result = merged[
        [
            "originsiteid",
            "destsiteid",
            "relative_delay",
        ]
    ].copy()

    result = result.rename(
        columns={
            "relative_delay":
                lag_name
        }
    )

    return result


# ============================================================
# 4. BUILD EXACT M1 FEATURES
# ============================================================

def build_features(
    live,
    history,
):

    network = pd.read_parquet(
        NETWORK_PATH
    )

    latest_timestamp = (
        live["timestamp"].max()
    )

    current = (
        live[
            live["timestamp"]
            == latest_timestamp
        ]
        .copy()
    )

    features = current.rename(
        columns={
            "relative_delay": "rd_now",
        }
    )[
        [
            "timestamp",
            "originsiteid",
            "destsiteid",
            "source_id",
            "source_name",
            "coordinates",
            "mintt",
            "rd_now",
        ]
    ].copy()

    # ------------------------------
    # Historical traffic features
    # ------------------------------

    for lag in LAG_MINUTES:

        lag_values = attach_lag(
            current,
            history,
            lag,
        )

        features = features.merge(
            lag_values,
            on=[
                "originsiteid",
                "destsiteid",
            ],
            how="left",
            validate="one_to_one",
        )

    for lag in [5, 15, 30]:

        features[
            f"rd_change_{lag}m"
        ] = (
            features["rd_now"]
            - features[
                f"rd_lag_{lag}m"
            ]
        )

    # ------------------------------
    # Canberra-local time features
    # ------------------------------

    local_time = (
        features["timestamp"]
        .dt.tz_convert(
            "Australia/Sydney"
        )
    )

    hour_decimal = (
        local_time.dt.hour
        + local_time.dt.minute / 60.0
        + local_time.dt.second / 3600.0
    )

    features["hour_sin"] = np.sin(
        2
        * np.pi
        * hour_decimal
        / 24.0
    )

    features["hour_cos"] = np.cos(
        2
        * np.pi
        * hour_decimal
        / 24.0
    )

    dow = local_time.dt.dayofweek

    features["dow_sin"] = np.sin(
        2
        * np.pi
        * dow
        / 7.0
    )

    features["dow_cos"] = np.cos(
        2
        * np.pi
        * dow
        / 7.0
    )

    features["is_weekend"] = (
        dow >= 5
    ).astype(float)

    # ------------------------------
    # Authoritative training-time
    # static feature definitions
    # ------------------------------

    static_columns = [
        "originsiteid",
        "destsiteid",

        "length_m",
        "minnumberoflanes",
        "isfreeway",

        "origin_x_m",
        "origin_y_m",
        "destination_x_m",
        "destination_y_m",
        "midpoint_x_m",
        "midpoint_y_m",

        "bearing_sin",
        "bearing_cos",
        "geography_resolved",
    ]

    features = features.merge(
        network[static_columns],
        on=[
            "originsiteid",
            "destsiteid",
        ],
        how="left",
        validate="one_to_one",
    )

    if len(features) != len(live):
        raise RuntimeError(
            "Feature construction changed "
            "live row count."
        )

    return features


# ============================================================
# 5. FROZEN M1 INFERENCE
# ============================================================

def run_inference(features):

    X = (
        features[M1_FEATURES]
        .astype(np.float32)
        .to_numpy()
    )

    if X.shape[1] != 25:
        raise RuntimeError(
            f"Expected 25 M1 features, "
            f"found {X.shape[1]}."
        )

    for horizon in HORIZONS:

        model_path = (
            MODEL_DIR
            / f"m1_{horizon}min.txt"
        )

        if not model_path.exists():
            raise FileNotFoundError(
                model_path
            )

        model = lgb.Booster(
            model_file=str(model_path)
        )

        if (
            list(model.feature_name())
            != M1_FEATURES
        ):
            raise RuntimeError(
                f"{horizon}m model feature "
                "order does not match "
                "production M1."
            )

        features[
            f"pred_rd_{horizon}m"
        ] = model.predict(
            X,
            num_iteration=(
                model.best_iteration
                if model.best_iteration > 0
                else None
            ),
        )

    return features


# ============================================================
# 6. FORECAST STATUS
# ============================================================

def assign_status(features):

    lag_columns = [
        "rd_lag_5m",
        "rd_lag_15m",
        "rd_lag_30m",
        "rd_lag_60m",
    ]

    full_history = (
        features[lag_columns]
        .notna()
        .all(axis=1)
    )

    features["forecast_status"] = (
        np.where(
            features["rd_now"].isna(),
            "data_unavailable",
            np.where(
                full_history,
                "full_history",
                "warming_up",
            ),
        )
    )

    features[
        "forecast_generated_at"
    ] = pd.Timestamp.now(tz="UTC")

    return features


# ============================================================
# 7. SAVE PARQUET + FRONTEND JSON
# ============================================================

def absolute_delay_seconds(relative_delay, mintt):

    if (
        relative_delay is None
        or mintt is None
        or not np.isfinite(relative_delay)
        or not np.isfinite(mintt)
        or mintt <= 0
    ):
        return None

    return float(relative_delay * mintt)


def travel_time_seconds(relative_delay, mintt):

    delay = absolute_delay_seconds(
        relative_delay,
        mintt,
    )

    if delay is None:
        return None

    return float(mintt + delay)

def save_outputs(features):

    FORECAST_PARQUET_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    JSON_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    parquet_columns = [
        "timestamp",
        "forecast_generated_at",
        "forecast_status",

        "originsiteid",
        "destsiteid",
        "source_id",
        "source_name",
        "coordinates",

        "rd_now",
        "mintt",

        "rd_lag_5m",
        "rd_lag_15m",
        "rd_lag_30m",
        "rd_lag_60m",

        "pred_rd_15m",
        "pred_rd_30m",
        "pred_rd_60m",
        "pred_rd_120m",
    ]

    forecasts = (
        features[parquet_columns]
        .copy()
    )

    forecasts.to_parquet(
        FORECAST_PARQUET_PATH,
        index=False,
    )

    latest_timestamp = (
        forecasts["timestamp"].max()
    )

    generated_at = (
        forecasts[
            "forecast_generated_at"
        ].max()
    )

    status_counts = (
        forecasts[
            "forecast_status"
        ]
        .value_counts()
        .to_dict()
    )

    links = []

    for row in forecasts.itertuples(index=False):

        status = row.forecast_status

        usable = (
            status != "data_unavailable"
        )

        mintt = float_or_none(
            row.mintt
        )

        current = float_or_none(
            row.rd_now
        )

        forecast_15 = (
            float_or_none(row.pred_rd_15m)
            if usable else None
        )

        forecast_30 = (
            float_or_none(row.pred_rd_30m)
            if usable else None
        )

        forecast_60 = (
            float_or_none(row.pred_rd_60m)
            if usable else None
        )

        forecast_120 = (
            float_or_none(row.pred_rd_120m)
            if usable else None
        )

        links.append(
            {
                "origin": int_or_none(
                    row.originsiteid
                ),

                "destination": int_or_none(
                    row.destsiteid
                ),

                "source_id": int_or_none(
                    row.source_id
                ),

                "name": row.source_name,

                "status": status,

                # Relative delay
                "current": current,
                "forecast_15": forecast_15,
                "forecast_30": forecast_30,
                "forecast_60": forecast_60,
                "forecast_120": forecast_120,

                # Free-flow travel time
                "mintt_seconds": mintt,

                # Absolute delay (seconds)
                "current_delay_seconds":
                    absolute_delay_seconds(
                        current, mintt
                    ),

                "forecast_15_delay_seconds":
                    absolute_delay_seconds(
                        forecast_15, mintt
                    ),

                "forecast_30_delay_seconds":
                    absolute_delay_seconds(
                        forecast_30, mintt
                    ),

                "forecast_60_delay_seconds":
                    absolute_delay_seconds(
                        forecast_60, mintt
                    ),

                "forecast_120_delay_seconds":
                    absolute_delay_seconds(
                        forecast_120, mintt
                    ),

                # Expected total travel time (seconds)
                "current_travel_time_seconds":
                    travel_time_seconds(
                        current, mintt
                    ),

                "forecast_15_travel_time_seconds":
                    travel_time_seconds(
                        forecast_15, mintt
                    ),

                "forecast_30_travel_time_seconds":
                    travel_time_seconds(
                        forecast_30, mintt
                    ),

                "forecast_60_travel_time_seconds":
                    travel_time_seconds(
                        forecast_60, mintt
                    ),

                "forecast_120_travel_time_seconds":
                    travel_time_seconds(
                        forecast_120, mintt
                    ),

                "geometry": row.coordinates,
            }
        )

    payload = {
        "generated_at":
            iso_or_none(generated_at),

        "traffic_timestamp":
            iso_or_none(latest_timestamp),

        "link_count":
            len(links),

        "status_counts":
            {
                str(k): int(v)
                for k, v
                in status_counts.items()
            },

        "links":
            links,
    }

    with open(
        JSON_PATH,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            payload,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return forecasts, payload


# ============================================================
# 8. MAIN
# ============================================================

def main():

    print(
        "Fetching live Canberra traffic..."
    )

    live = fetch_live_snapshot()

    traffic_timestamp = (
        live["timestamp"].max()
    )

    print(
        "Traffic timestamp:",
        traffic_timestamp,
    )

    print(
        "Live directed links:",
        len(live),
    )

    print(
        "Valid current relative delay:",
        int(
            live[
                "relative_delay"
            ].notna().sum()
        ),
    )

    print()
    print(
        "Updating rolling history..."
    )

    history = update_history(live)

    print(
        "History rows:",
        f"{len(history):,}",
    )

    print(
        "History timestamps:",
        history[
            "timestamp"
        ].nunique(),
    )

    print()
    print(
        "Building M1 features..."
    )

    features = build_features(
        live,
        history,
    )

    for lag in LAG_MINUTES:

        col = f"rd_lag_{lag}m"

        print(
            f"{col}: "
            f"{features[col].notna().sum()}"
            f"/{len(features)}"
        )

    print()
    print(
        "Running frozen M1 models..."
    )

    features = run_inference(
        features
    )

    features = assign_status(
        features
    )

    forecasts, payload = (
        save_outputs(features)
    )

    print()
    print(
        "Forecast status:"
    )

    print(
        forecasts[
            "forecast_status"
        ]
        .value_counts()
        .to_string()
    )

    print()
    print(
        "Saved:",
        FORECAST_PARQUET_PATH,
    )

    print(
        "Saved:",
        JSON_PATH,
    )

    print()
    print(
        "Production update complete."
    )


if __name__ == "__main__":
    main()

