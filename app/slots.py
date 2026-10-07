from datetime import date, datetime, timedelta


def generate_slots(open_hour: int, close_hour: int, step: int) -> list[str]:
    out, m = [], open_hour * 60
    while m + step <= close_hour * 60:
        out.append(f"{m // 60:02d}:{m % 60:02d}")
        m += step
    return out


def today_str() -> str:
    return date.today().isoformat()


def now_time() -> str:
    return datetime.now().strftime("%H:%M")


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def is_valid_date(s) -> bool:
    try:
        return date.fromisoformat(s).isoformat() == s
    except (ValueError, TypeError):
        return False
