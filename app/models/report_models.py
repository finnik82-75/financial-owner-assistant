from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class KPISummary(BaseModel):
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    net_profit: Optional[float] = None
    fcf: Optional[float] = None


class QualityReport(BaseModel):
    score: int = 100
    issues: list[str] = []
    warnings: list[str] = []


class AnalysisReport(BaseModel):
    session_id: str
    created_at: datetime
    kpis: KPISummary
    quality: QualityReport
    analysis: str
    recommendations: list[str] = []
