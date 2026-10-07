from dotenv import load_dotenv
import os
from sqlalchemy import create_engine

# Initializing the environment
load_dotenv()
RAILWAY_DB_URL = os.getenv("RAILWAY_DB_URL")

# Initializing DB connection
def db_engine():
    engine = create_engine(
        RAILWAY_DB_URL,
        pool_pre_ping=True,
        pool_recycle=1800
    )
    return engine