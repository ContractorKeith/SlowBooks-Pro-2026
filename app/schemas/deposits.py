from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from app.schemas.common import StrictModel


class PendingDepositResponse(BaseModel):
    transaction_line_id: int
    transaction_id: int
    date: dt_date
    description: str
    reference: str = ""
    source_type: str = ""
    amount: float


class DepositCreate(StrictModel):
    deposit_to_account_id: int
    date: dt_date
    total: Decimal
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    line_ids: list[int] = []
