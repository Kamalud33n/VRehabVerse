from models import SessionModel


def session_summary(s: SessionModel) -> dict:
    return {
        "session_id": s.id,
        "exercise_name": s.exercise_name,
        "duration_seconds": s.duration_seconds,
        "total_reps": s.total_reps,
        "accuracy": s.final_accuracy,
        "rom": s.final_rom,
        "status": s.status,
        "date": s.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def average_or_zero(values: list) -> float:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else 0.0