"""
config.py
---------
Central place for settings like the database password, instead of
typing it directly into every file. This file should NOT be shared
publicly (e.g. don't upload it to GitHub) - it's local-only.

Why this matters: if your password is typed directly in dashboard.py,
app.py, AND setup_database.py, changing it means editing 3 files
and hoping you don't miss one. Worse, if you ever share your code
(e.g. showing your teacher, uploading to GitHub for your project
submission), the password goes with it. Keeping it in ONE file that
you exclude from sharing fixes both problems.

Cloud support: DB_CONFIG now switches between local MySQL (day-to-day
development) and the Aiven cloud MySQL instance (used once the app is
actually deployed), based on the USE_CLOUD_DB flag in .env. Default is
local, so nothing changes for existing local development unless someone
deliberately flips the flag.
"""

import os
from dotenv import load_dotenv

load_dotenv()

USE_CLOUD_DB = os.getenv("USE_CLOUD_DB", "false").lower() == "true"

if USE_CLOUD_DB:
    # Aiven cloud MySQL - all values come from .env, never hardcoded here.
    # Aiven requires SSL, so ssl_disabled must stay False for this branch.
    DB_CONFIG = {
        "host": os.getenv("CLOUD_DB_HOST"),
        "port": int(os.getenv("CLOUD_DB_PORT", 3306)),
        "user": os.getenv("CLOUD_DB_USER"),
        "password": os.getenv("CLOUD_DB_PASSWORD"),
        "database": os.getenv("CLOUD_DB_NAME", "defaultdb"),
        "ssl_disabled": False
    }
else:
    # Local MySQL - unchanged from before
    DB_CONFIG = {
        "host": "localhost",
        "user": "root",
        "password": "root2008",   # <------ change here if password changes, nowhere else!
        "database": "school_bot"
    }
