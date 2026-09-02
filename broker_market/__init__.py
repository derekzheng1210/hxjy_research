from .storage import (
    EMOTION_HISTORY_PATH,
    MARKET_DIR,
    calculate_ofr_movers,
    calculate_market_emotion,
    data_version,
    ensure_directories,
    load_emotion_history,
    load_preferences,
    load_snapshot,
    merge_bond_rows,
    record_market_emotion,
    save_preferences,
)


def public_status(*args, **kwargs):
    from .scheduler import public_status as implementation
    return implementation(*args, **kwargs)


def start_scheduler(*args, **kwargs):
    from .scheduler import start_scheduler as implementation
    return implementation(*args, **kwargs)


def trigger_update(*args, **kwargs):
    from .scheduler import trigger_update as implementation
    return implementation(*args, **kwargs)

__all__ = [
    "EMOTION_HISTORY_PATH", "MARKET_DIR", "calculate_market_emotion", "calculate_ofr_movers",
    "data_version", "ensure_directories", "load_emotion_history",
    "load_preferences", "load_snapshot", "merge_bond_rows",
    "record_market_emotion", "public_status", "save_preferences",
    "start_scheduler", "trigger_update",
]
