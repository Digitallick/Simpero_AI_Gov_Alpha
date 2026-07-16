from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    environment: str


class DbHealthResponse(BaseModel):
    status: str
    database: str


class QueueHealthResponse(BaseModel):
    status: str
    queue: str
