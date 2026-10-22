"""应用配置（可通过环境变量覆盖）。"""

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "rebuild-dev-secret-change-me")
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_DB = os.environ.get("MONGO_DB", "survey_rebuild_db")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # 测试中可关闭；生产环境保持开启
    CSRF_ENABLED = True
    MAX_CONTENT_LENGTH = 1024 * 1024

    # 单题统计每页展示的文本/数字答案数
    STATS_ANSWER_LIMIT = 500
