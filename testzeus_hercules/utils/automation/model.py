# models.py
from sqlalchemy import Column, String
import uuid
from testzeus_hercules.utils.automation.db import Base  # Use the shared Base

class User(Base):
    __tablename__ = 'automation_sequence'
    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid1().hex)
    name = Column(String(50), nullable=False)
    parameters = Column(String(50), nullable=False)
    imports = Column(String(50), nullable=True)
