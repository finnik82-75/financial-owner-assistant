from pydantic import BaseModel
from typing import Optional
from datetime import date


class BDRRow(BaseModel):
    name: str
    plan: Optional[float] = None
    fact: Optional[float] = None
    deviation: Optional[float] = None


class DDSRow(BaseModel):
    name: str
    inflow: Optional[float] = None
    outflow: Optional[float] = None
    balance: Optional[float] = None


class FinancialReport(BaseModel):
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    bdr: list[BDRRow] = []
    dds: list[DDSRow] = []
    currency: str = "RUB"
