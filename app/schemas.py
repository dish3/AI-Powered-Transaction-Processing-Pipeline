import uuid
from datetime import datetime, date
from typing import List, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models import JobStatus

class HealthResponse(BaseModel):
    status: str = "healthy"

class JobUploadResponse(BaseModel):
    job_id: uuid.UUID = Field(validation_alias="id")
    status: JobStatus
    filename: str
    row_count_raw: int

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class JobStatusResponse(BaseModel):
    job_id: uuid.UUID = Field(validation_alias="id")
    status: JobStatus
    filename: str
    row_count_raw: int
    row_count_clean: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class JobListEntry(BaseModel):
    job_id: uuid.UUID = Field(validation_alias="id")
    filename: str
    status: JobStatus
    row_count_raw: int
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class TransactionResponse(BaseModel):
    txn_id: Optional[str] = None
    date: date
    merchant: str
    amount: float
    currency: str
    status: str
    category: str
    account_id: str
    notes: Optional[str] = None
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    llm_category: Optional[str] = None
    llm_failed: bool

    model_config = ConfigDict(from_attributes=True)

class AnomalyResponse(BaseModel):
    txn_id: Optional[str] = None
    date: date
    merchant: str
    amount: float
    currency: str
    account_id: str
    anomaly_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class MerchantSpend(BaseModel):
    merchant: str
    spend: float
    count: int

class LLMSummaryResponse(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: List[MerchantSpend]
    anomaly_count: int
    narrative: str
    risk_level: str

    model_config = ConfigDict(from_attributes=True)

class JobResultsResponse(BaseModel):
    job_id: uuid.UUID = Field(validation_alias="id")
    status: JobStatus
    summary: Optional[LLMSummaryResponse] = None
    category_breakdown: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    anomalies: List[AnomalyResponse] = Field(default_factory=list)
    transactions: List[TransactionResponse] = Field(default_factory=list)

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
