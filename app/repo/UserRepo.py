from sqlalchemy.ext.asyncio import AsyncSession
from app.models.organisation import Users
from app.repo.BaseRepo import BaseRepo



class UserRepo(BaseRepo[Users, dict]):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def create(self, data: dict, **kwargs: object) -> Users:
        """Create a new user in the database."""
        new_user = Users(**data)
        self.session.add(new_user)
        return new_user

    async def get_by_id(self, id: int) -> Users | None:
        return await self.session.get(Users, id)