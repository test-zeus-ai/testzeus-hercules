import uuid
from collections.abc import AsyncGenerator
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi_users import FastAPIUsers, BaseUserManager, schemas as fa_models
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase, SQLAlchemyBaseUserTableUUID
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Table, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

# ----- Configuration -----
DATABASE_URL = "sqlite+aiosqlite:///./test.db"
SECRET = "SECRET"


# ----- Database Setup -----
class Base(DeclarativeBase):
    pass


# Define a join table for many-to-many relationship between Orders and Items.
order_items = Table(
    "order_items",
    Base.metadata,
    Column("order_id", Integer, ForeignKey("orders.id"), primary_key=True),
    Column("item_id", Integer, ForeignKey("items.id"), primary_key=True),
)


# Define the User model using UUIDs.
class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"
    # Relationships to extra entities:
    items = relationship("Item", back_populates="owner")
    orders = relationship("Order", back_populates="user")


# Define additional entities.
class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text, nullable=True)
    items = relationship("Item", back_populates="category")


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    owner_id = Column(String, ForeignKey("users.id"))
    owner = relationship("User", back_populates="items")
    category_id = Column(Integer, ForeignKey("categories.id"))
    category = relationship("Category", back_populates="items")
    orders = relationship("Order", secondary=order_items, back_populates="items")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))
    user = relationship("User", back_populates="orders")
    items = relationship("Item", secondary=order_items, back_populates="orders")


# Create asynchronous engine and session maker.
engine = create_async_engine(DATABASE_URL, echo=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


# Dependency: Provide fastapi-users database adapter.
async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase[User, uuid.UUID], None]:
    yield SQLAlchemyUserDatabase(session, User)


# ----- Authentication Configuration -----
bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ----- fastapi-users Setup -----
class UserRead(fa_models.BaseUser[uuid.UUID]):
    pass


class UserCreate(fa_models.BaseUserCreate):
    pass


class UserUpdate(fa_models.BaseUserUpdate):
    pass


class UserManager(BaseUserManager[User, uuid.UUID]):
    user_db_model = User
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    # Implement parse_id to convert the incoming string to a UUID.
    def parse_id(self, id: str) -> uuid.UUID:
        return uuid.UUID(id)

    async def on_after_register(self, user: User, request=None):
        print(f"User {user.id} has registered.")


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase[User, uuid.UUID] = Depends(get_user_db)
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])


# ----- Pydantic Schemas for Additional Entities -----
class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None


class CategoryRead(CategoryCreate):
    id: int

    class Config:
        orm_mode = True


class ItemCreate(BaseModel):
    title: str
    description: str
    category_id: int


class ItemRead(ItemCreate):
    id: int
    owner_id: str

    class Config:
        orm_mode = True


class OrderCreate(BaseModel):
    user_id: str
    item_ids: List[int]


class OrderRead(BaseModel):
    id: int
    user_id: str
    items: List[ItemRead] = []

    class Config:
        orm_mode = True


# ----- FastAPI Application Setup -----
app = FastAPI(title="Async FastAPI Users with Extra Entities", version="1.0.0")

# Include fastapi-users routes.
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/auth/users",
    tags=["auth"],
)


# Endpoints for Categories.
@app.post("/categories", response_model=CategoryRead)
async def create_category(
    category: CategoryCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    # Check if category with the same name already exists.
    result = await session.execute(
        select(Category).filter(Category.name == category.name)
    )
    existing_category = result.scalar_one_or_none()
    if existing_category:
        raise HTTPException(
            status_code=400,
            detail=f"Category with name '{category.name}' already exists.",
        )
    new_category = Category(**category.dict())
    session.add(new_category)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail="Failed to create category due to integrity error."
        )
    await session.refresh(new_category)
    return new_category


@app.get("/categories", response_model=List[CategoryRead])
async def list_categories(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    result = await session.execute(select(Category))
    categories = result.scalars().all()
    return categories


# Endpoints for Items.
@app.post("/items", response_model=ItemRead)
async def create_item(
    item: ItemCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    # Ensure the category exists.
    result = await session.execute(
        select(Category).filter(Category.id == item.category_id)
    )
    category_obj = result.scalar_one_or_none()
    if category_obj is None:
        raise HTTPException(
            status_code=404, detail=f"Category with id {item.category_id} not found."
        )

    new_item = Item(**item.dict(), owner_id=str(current_user.id))
    session.add(new_item)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail="Failed to create item due to integrity error."
        )
    await session.refresh(new_item)
    return new_item


@app.get("/items", response_model=List[ItemRead])
async def list_items(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    result = await session.execute(select(Item))
    items = result.scalars().all()
    return items


# Endpoints for Orders.
@app.post("/orders", response_model=OrderRead)
async def create_order(
    order: OrderCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    if str(current_user.id) != order.user_id:
        raise HTTPException(status_code=403, detail="Not allowed")
    items = []
    for item_id in order.item_ids:
        result = await session.execute(select(Item).filter(Item.id == item_id))
        item_obj = result.scalar_one_or_none()
        if item_obj is None:
            raise HTTPException(
                status_code=404, detail=f"Item with id {item_id} not found"
            )
        items.append(item_obj)
    new_order = Order(user_id=order.user_id, items=items)
    session.add(new_order)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail="Failed to create order due to integrity error."
        )
    await session.refresh(new_order)
    return new_order


@app.get("/orders", response_model=List[OrderRead])
async def list_orders(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(fastapi_users.current_user(active=True)),
):
    result = await session.execute(select(Order))
    orders = result.scalars().all()
    return orders


# Create tables on startup.
@app.on_event("startup")
async def on_startup():
    await create_db_and_tables()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
