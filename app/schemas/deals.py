from pydantic import BaseModel

# Placeholder schema — extend with real Deal model fields when the Deal model is implemented.


class DealResponse(BaseModel):
    id: str
    name: str
    org_id: str
