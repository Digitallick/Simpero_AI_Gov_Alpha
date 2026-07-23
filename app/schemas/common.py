from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base for response models that must match the frozen tRPC wire shapes
    in Simpero_AI_Gov_Web's src/api/_legacy/server/routers.d.ts — camelCase
    keys on the wire, snake_case attributes in Python. FastAPI serializes
    responses by alias by default, so declaring fields snake_case here is
    enough; no per-field Field(alias=...) needed.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class SuccessResponse(CamelModel):
    success: bool
