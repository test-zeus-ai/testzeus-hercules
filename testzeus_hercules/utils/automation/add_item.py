# add_user.py
from testzeus_hercules.utils.automation.db import SessionLocal
from testzeus_hercules.utils.automation.model import User

def add_method(name, parameters, imports=None):
    session = SessionLocal()
    try:
        user = User(name=name, parameters=parameters, imports = imports)
        session.add(user)
        session.commit()
        print("User added")
    except: 
        print("User not added")
    finally:
        session.close()
