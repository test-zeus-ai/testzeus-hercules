# init_db.py
from testzeus_hercules.utils.automation.db import engine
from testzeus_hercules.utils.automation.model import User, Base

def init_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)