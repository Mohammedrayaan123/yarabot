"""
config.py
---------
DB credentials in one place instead of duplicated across dashboard.py/
app.py/setup_database.py.

DB_CONFIG switches between local MySQL and Aiven cloud MySQL based on
USE_CLOUD_DB in .env - defaults to local.
"""

import os
from dotenv import load_dotenv

load_dotenv()

USE_CLOUD_DB = os.getenv("USE_CLOUD_DB", "false").lower() == "true"

if USE_CLOUD_DB:
    # Aiven requires SSL - ssl_disabled must stay False here.
    DB_CONFIG = {
        "host": os.getenv("CLOUD_DB_HOST"),
        "port": int(os.getenv("CLOUD_DB_PORT", 3306)),
        "user": os.getenv("CLOUD_DB_USER"),
        "password": os.getenv("CLOUD_DB_PASSWORD"),
        "database": os.getenv("CLOUD_DB_NAME", "defaultdb"),
        "ssl_disabled": False
    }
else:
    DB_CONFIG = {
        "host": "localhost",
        "user": "root",
        "password": "root2008",
        "database": "school_bot"
    }
