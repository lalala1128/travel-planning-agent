import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


def create_llm_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise RuntimeError("没有配置 DEEPSEEK_API_KEY")

    return OpenAI(
        api_key=api_key,
        base_url=os.getenv(
            "DEEPSEEK_BASE_URL",
            "https://api.deepseek.com",
        ),
    )


def get_llm_model() -> str:
    return os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-flash",
    )