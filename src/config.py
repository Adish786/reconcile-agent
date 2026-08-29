"""
Configuration management for the Reconcile Agent.

This module loads environment variables and `.env` file content,
validates them, and makes them available as a typed `Settings` object.
"""

import os
from typing import Optional

from pydantic_settings import BaseSettings  # requires: poetry add pydantic-settings


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and .env file.

    All fields have default values, but can be overridden by:
    - Setting environment variables (e.g., `OPENAI_API_KEY=...`)
    - Creating a `.env` file in the project root with key=value pairs.

    Attributes:
        OPENAI_API_KEY: API key for OpenAI-compatible LLM (Gemini, OpenAI, etc.)
        OPENAI_BASE_URL: Base URL for the LLM API endpoint.
        DATABASE_URL: SQLAlchemy database URL (SQLite, PostgreSQL, etc.)
        LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        MAX_ITERATIONS: Maximum retries/iterations for agent workflows.
        CONFIDENCE_THRESHOLD: Minimum confidence score to auto-approve a match.
    """

    # LLM configuration
    OPENAI_API_KEY: str = ""  # set via env or .env
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # Database
    DATABASE_URL: str = "sqlite:///./test.db"

    # Logging
    LOG_LEVEL: str = "INFO"

    # Agent parameters
    MAX_ITERATIONS: int = 5
    CONFIDENCE_THRESHOLD: float = 0.95

    class Config:
        """
        Pydantic configuration for loading settings.
        """
        # Name of the .env file (located in the project root)
        env_file = ".env"
        # Encoding of the .env file
        env_file_encoding = "utf-8"
        # Case-sensitive environment variables (e.g., OPENAI_API_KEY)
        case_sensitive = True


# Create a singleton instance of the settings that can be imported anywhere
settings = Settings()