import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")  
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "5"))
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.95"))

settings = Settings()