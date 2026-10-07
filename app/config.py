import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    admin_password: str = "change-me"
    jwt_secret: str = "dev-secret-change-me"
    db_file: str = "data/queueup.db"
    open_hour: int = 9
    close_hour: int = 17
    step: int = 30
    reminder_minutes: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            admin_password=e("ADMIN_PASSWORD", "change-me"),
            jwt_secret=e("JWT_SECRET", "dev-secret-change-me"),
            db_file=e("DB_FILE", "data/queueup.db"),
            open_hour=int(e("OPEN_HOUR", "9")),
            close_hour=int(e("CLOSE_HOUR", "17")),
            step=int(e("SLOT_MINUTES", "30")),
            reminder_minutes=int(e("REMINDER_MINUTES", "60")),
        )
