from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")
S = TypeVar("S")


class BaseRepo(ABC, Generic[T, S]):
    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session

    @abstractmethod
    async def create(self, data: S, **kwargs: object) -> T:
        pass

    @abstractmethod
    async def get_by_id(self, id: int) -> T | None:
        pass
