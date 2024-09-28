from datetime import datetime

from sqlmodel import Field, SQLModel


class ASSActiveAlarm(SQLModel, table=True):  # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    name: str
    scheduled_time: datetime
