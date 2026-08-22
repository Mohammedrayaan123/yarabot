# ============================================================
# CLOUD-AWARE CONFIG — reference code for Claude Code
# ============================================================
# Updates config.py to support BOTH local MySQL (for continued
# local development/testing) and Aiven cloud MySQL (for hosting),
# switching based on an environment variable - so Rayaan doesn't
# lose local testing capability while setting up cloud hosting.
# ============================================================

"""
config.py (updated)
--------------------
Switches between local and cloud database based on USE_CLOUD_DB
in .env. Default is local (False) so nothing breaks for existing
local development unless explicitly switched on.
"""

import os
from dotenv import load_dotenv

load_dotenv()

USE_CLOUD_DB = os.getenv("USE_CLOUD_DB", "false").lower() == "true"

if USE_CLOUD_DB:
    # Aiven cloud MySQL - values come from .env, never hardcoded
    DB_CONFIG = {
        "host": os.getenv("CLOUD_DB_HOST"),
        "port": int(os.getenv("CLOUD_DB_PORT", 3306)),
        "user": os.getenv("CLOUD_DB_USER"),
        "password": os.getenv("CLOUD_DB_PASSWORD"),
        "database": os.getenv("CLOUD_DB_NAME", "defaultdb"),
        "ssl_disabled": False  # Aiven requires SSL
    }
else:
    # Local MySQL - unchanged from before
    DB_CONFIG = {
        "host": "localhost",
        "user": "root",
        "password": "root2008",
        "database": "school_bot"
    }

# ---------------------------------------------------------
# ADD to .env:
# ---------------------------------------------------------
# USE_CLOUD_DB=false          <- keep false for local testing
#
# CLOUD_DB_HOST=<from Aiven overview page>
# CLOUD_DB_PORT=<from Aiven overview page>
# CLOUD_DB_USER=<from Aiven overview page>
# CLOUD_DB_PASSWORD=<from Aiven overview page>
# CLOUD_DB_NAME=defaultdb
#
# To switch to cloud database, just change USE_CLOUD_DB to true
# and restart the app/scripts.
