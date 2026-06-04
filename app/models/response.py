from pydantic import BaseModel


class MetricsResponse(BaseModel):

    store_id: str

    unique_visitors: int

    total_revenue: float

    total_transactions: int

    conversion_rate: float