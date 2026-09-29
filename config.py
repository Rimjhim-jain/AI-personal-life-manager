import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
JEV_API_KEY = os.getenv("JEV_API_KEY", "")
DB_PATH = os.getenv("DB_PATH", "data.db")

# Confidence thresholds for JEV/Groq classifier output
CONFIDENCE_HIGH = 0.90    # Save silently
CONFIDENCE_MEDIUM = 0.65  # Save but ask for confirmation
# Below CONFIDENCE_MEDIUM: don't save, ask user to pick category
