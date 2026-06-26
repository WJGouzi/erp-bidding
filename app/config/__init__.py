import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")
REFERENCE_ENV_FILE = BASE_DIR.parent / "erp_ai_bidding" / "backend" / ".env.prod"
if REFERENCE_ENV_FILE.exists():
    load_dotenv(REFERENCE_ENV_FILE, override=False)


class Config:
    """集中定义 Flask 应用、存储、模型与后台任务相关配置。"""

    APP_NAME = os.getenv("APP_NAME", "ERP Bidding Backend")
    APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    RESTX_MASK_SWAGGER = False
    RESTX_ERROR_404_HELP = False

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URI",
        "mysql+pymysql://root:12345678@127.0.0.1:3306/erp?charset=utf8mb4",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 120,
    }

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 50 * 1024 * 1024))
    STORAGE_DIR = str(BASE_DIR / "storage")

    CHROMA_HOST = os.getenv("CHROMA_HOST", "116.63.183.113")
    CHROMA_PORT = int(os.getenv("CHROMA_PORT", 18080))
    CHROMA_TENANT = os.getenv("CHROMA_TENANT", "erp")
    CHROMA_DATABASE = os.getenv("CHROMA_DATABASE", "bidding")
    CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "tender")

    PRODUCT_CHROMA_TENANT = os.getenv("PRODUCT_CHROMA_TENANT", "erp")
    PRODUCT_CHROMA_DATABASE = os.getenv("PRODUCT_CHROMA_DATABASE", "erp")
    PRODUCT_CHROMA_COLLECTION = os.getenv("PRODUCT_CHROMA_COLLECTION", "product")

    BAIDU_OCR_JOB_URL = os.getenv("BAIDU_OCR_JOB_URL", "")
    BAIDU_OCR_TOKEN = os.getenv("BAIDU_OCR_TOKEN", "")
    BAIDU_OCR_MODEL = os.getenv("BAIDU_OCR_MODEL", "PP-OCRv5")
    BAIDU_OCR_POLL_INTERVAL_SECONDS = float(
        os.getenv("BAIDU_OCR_POLL_INTERVAL_SECONDS", "1")
    )
    BAIDU_OCR_MAX_WAIT_SECONDS = float(
        os.getenv("BAIDU_OCR_MAX_WAIT_SECONDS", "20")
    )
    BAIDU_OCR_REQUEST_TIMEOUT_SECONDS = float(
        os.getenv("BAIDU_OCR_REQUEST_TIMEOUT_SECONDS", "30")
    )

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
    OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "qwen-long")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1500"))

    # ChromaDB 直连配置
    CHROMA_SSL = os.getenv("CHROMA_SSL", "false").lower() == "true"
    CHROMA_MAX_RETRIES = int(os.getenv("CHROMA_MAX_RETRIES", "2"))
    CHROMA_AUTO_PROVISION = os.getenv("CHROMA_AUTO_PROVISION", "true").lower() == "true"

    # 通义千问 Embedding 配置
    QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
    QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    QWEN_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4")

    # PaddleOCR 配置
    PADDLE_OCR_JOB_URL = os.getenv("PADDLE_OCR_JOB_URL", "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs")
    PADDLE_OCR_TOKEN = os.getenv("PADDLE_OCR_TOKEN", "")
    PADDLE_OCR_MODEL = os.getenv("PADDLE_OCR_MODEL", "PP-OCRv5")
    PADDLE_OCR_POLL_INTERVAL = float(os.getenv("PADDLE_OCR_POLL_INTERVAL", "1.0"))
    PADDLE_OCR_MAX_POLL_SECONDS = float(os.getenv("PADDLE_OCR_MAX_POLL_SECONDS", "120"))

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "116.63.183.113:29000")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
    MINIO_BUCKET_NAME = os.getenv("MINIO_BUCKET_NAME", "bidding-template")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

    ANALYZE_ASYNC = os.getenv("ANALYZE_ASYNC", "true").lower() == "true"
    ANALYZE_SIMULATE_DELAY = float(os.getenv("ANALYZE_SIMULATE_DELAY", "0"))
    TASK_EXECUTOR_MAX_WORKERS = int(
        os.getenv("TASK_EXECUTOR_MAX_WORKERS", "10")
    )
    TASK_EXECUTION_TIMEOUT_SECONDS = float(
        os.getenv("TASK_EXECUTION_TIMEOUT_SECONDS", "600")
    )
    GENERATE_ASYNC = os.getenv("GENERATE_ASYNC", "true").lower() == "true"
    GENERATE_SIMULATE_DELAY = float(os.getenv("GENERATE_SIMULATE_DELAY", "0"))
