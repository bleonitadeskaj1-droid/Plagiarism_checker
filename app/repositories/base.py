"""Generic repository with CRUD operations."""

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Base repository providing common database operations."""

    def __init__(self, session: Session, model: type[ModelType]) -> None:
        self.session = session
        self.model = model

    def get(self, entity_id: int) -> ModelType | None:
        """Return a single entity by primary key."""
        return self.session.get(self.model, entity_id)

    def get_all(self, skip: int = 0, limit: int = 100) -> list[ModelType]:
        """Return paginated list of entities."""
        stmt = select(self.model).offset(skip).limit(limit)
        return list(self.session.scalars(stmt).all())

    def create(self, **kwargs) -> ModelType:
        """Create and persist a new entity."""
        entity = self.model(**kwargs)
        self.session.add(entity)
        self.session.commit()
        self.session.refresh(entity)
        return entity

    def update(self, entity: ModelType, **kwargs) -> ModelType:
        """Update fields on an existing entity."""
        for key, value in kwargs.items():
            setattr(entity, key, value)
        self.session.commit()
        self.session.refresh(entity)
        return entity

    def delete(self, entity: ModelType) -> None:
        """Delete an entity from the database."""
        self.session.delete(entity)
        self.session.commit()
