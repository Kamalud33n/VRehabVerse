"""
Registry of per-game metric groups.

Different VR games/levels report completely different metrics — Virtual
Store tracks arm-reach/movement data, a future game might track something
else entirely. Rather than hardcoding a new SessionModel column (and every
place that reads it) per game forever, each game type is defined once
here: the keyword(s) used to detect it from a session's game_name/
level_name/exercise_name, and its field map (VR app field name -> DB key
-> display label/format). routers/ws.py and services/report_builder.py
both go through this registry instead of re-deriving any of it.
"""

# db_key -> (vr_field_name, display_label, value_format)
# value_format is purely for display formatting: "int", "float1", "float2",
# "percent", "meters", "seconds", "mps" (meters/sec).
VIRTUAL_STORE_FIELDS = {
    "total_arm_reach_distance": ("TotalArmReachDistance", "Total Arm Reach Distance", "meters"),
    "total_movement_duration":  ("TotalMovementDuration",  "Total Movement Duration",  "seconds"),
    "average_movement_speed":   ("AverageMovementSpeed",   "Average Movement Speed",   "mps"),
    "max_movement_range":       ("MaxMovementRange",       "Range of Motion",          "meters"),
    "successful_movements":     ("SuccessfulMovements",    "Successful Movements",     "int"),
    "repetition_count":         ("RepetitionCount",        "Repetition Count",         "int"),
    "movement_accuracy":        ("MovementAccuracy",       "Movement Accuracy",        "percent"),
    "success_rate":             ("SuccessRate",            "Success Rate",             "percent"),
    "completed_tasks":          ("CompletedTasks",         "Completed Tasks",          "int"),
    "total_tasks":              ("TotalTasks",             "Total Tasks",              "int"),
}

# db_keys listed here are still captured from the VR payload and written
# to session.game_metrics by extract_game_metrics() (so the data exists
# and is queryable), but are excluded from anything user-facing: the PDF
# report grid (report_builder._game_metrics_grid, via get_fields_for) and
# the frontend metrics schema (registry_as_json, used by session.html /
# analytics.html to render live + summary metric cards). Add a db_key
# here to hide it end-to-end without deleting the underlying data.
HIDDEN_METRICS = {
    "movement_accuracy",
}

# game_type -> {"display_name": ..., "keywords": (...), "fields": {...}}
# display_name is the name a therapist actually picks on the session page
# (session.html's game <select>) and sees everywhere else (session lists,
# reports). Anything that shows this game to a user — e.g. analytics.html's
# per-game selector — must use display_name, not a prettified version of
# the game_type key: the internal key ("virtual_store") and the
# user-facing name ("Arm Movement Analytics") are unrelated strings and
# auto-capitalizing the key ("Virtual Store") produces a name the
# therapist never chose and won't recognize.
# Order matters — first match wins in detect_game_type, so put more
# specific keywords first if two entries could ever overlap.
GAME_METRIC_REGISTRY = {
    "virtual_store": {
        "display_name": "Arm Movement Analytics",
        "keywords": ("virtual store", "shopping"),
        "fields": VIRTUAL_STORE_FIELDS,
    },
    # Future games register here the same way, e.g.:
    # "obstacle_course": {"display_name": "Obstacle Course", "keywords": ("obstacle",), "fields": {...}},
}


def detect_game_type(*names: str | None) -> str | None:
    """
    Matches any of the given names (game_name, level_name, exercise_name —
    pass as many as you have, in any order) against the registry's
    keywords, case-insensitively. Returns the matching game_type key, or
    None if nothing matches (plain/legacy session with no game-specific
    metric group — e.g. Warm Up, or a single-level exercise).
    """
    haystack = " ".join(n for n in names if n).lower()
    if not haystack:
        return None
    for game_type, entry in GAME_METRIC_REGISTRY.items():
        for keyword in entry["keywords"]:
            if keyword in haystack:
                return game_type
    return None


def extract_game_metrics(game_type: str | None, raw_payload: dict) -> dict | None:
    """
    Given a detected game_type and the raw VR session_end JSON payload
    (PascalCase field names, exactly as sent by the Unity app), pulls that
    game's fields into a plain {db_key: value} dict ready to store in
    SessionModel.game_metrics. Returns None if game_type is unknown or
    none of its fields were present in the payload.

    Deliberately uses the FULL field map (including HIDDEN_METRICS) so
    hidden metrics are still captured and stored — only the display paths
    (get_fields_for, registry_as_json) filter them out.
    """
    entry = GAME_METRIC_REGISTRY.get(game_type)
    if not entry:
        return None

    result = {}
    for db_key, (vr_field, _label, _fmt) in entry["fields"].items():
        value = raw_payload.get(vr_field)
        if value is not None:
            result[db_key] = value

    return result or None


def get_fields_for(game_type: str | None, include_hidden: bool = False):
    """Returns the field map (db_key -> (vr_field, label, format)) for a
    game_type, or None if unknown / no game-specific metric group.

    By default excludes HIDDEN_METRICS so callers like
    report_builder._game_metrics_grid never render them. Pass
    include_hidden=True only for internal/debug tooling that
    intentionally needs the full map."""
    entry = GAME_METRIC_REGISTRY.get(game_type)
    if not entry:
        return None
    if include_hidden:
        return entry["fields"]
    return {
        db_key: value
        for db_key, value in entry["fields"].items()
        if db_key not in HIDDEN_METRICS
    }


def registry_as_json() -> dict:
    """JSON-serializable view of the whole registry, served to the
    frontend (session.html, analytics.html) via GET /api/dashboard/
    game-metrics-schema so it can render whichever game's live/historical
    metrics generically — adding a new game only ever means an entry
    here, never a frontend code change. display_name is the label to
    show a user; game_type is only an internal key.

    HIDDEN_METRICS are omitted here too, so a generic/schema-driven
    frontend renderer never learns about them either."""
    return {
        game_type: {
            "display_name": entry.get("display_name", game_type),
            "keywords": list(entry["keywords"]),
            "fields": [
                {"key": db_key, "vr_field": vr_field, "label": label, "format": fmt}
                for db_key, (vr_field, label, fmt) in entry["fields"].items()
                if db_key not in HIDDEN_METRICS
            ],
        }
        for game_type, entry in GAME_METRIC_REGISTRY.items()
    }
