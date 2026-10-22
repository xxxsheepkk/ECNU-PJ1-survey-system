"""MongoDB 连接与索引初始化。

集合划分：
- users：账号；
- surveys：问卷元信息与公开链接；
- questions：题目、选项、跳转规则；
- responses：一次填写/提交的答卷。
"""

from flask import current_app, g
from pymongo import ASCENDING, DESCENDING, MongoClient

USERS = "users"
SURVEYS = "surveys"
QUESTIONS = "questions"
RESPONSES = "responses"


def init_db(app):
    client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=5000)
    db = client[app.config["MONGO_DB"]]
    app.extensions["mongo_client"] = client
    app.extensions["mongo_db"] = db
    ensure_indexes(db)


def get_db():
    if "db" not in g:
        g.db = current_app.extensions["mongo_db"]
    return g.db


def ensure_indexes(db):
    """创建索引（幂等）。部分唯一索引防止重复草稿。"""
    db[USERS].create_index("username", unique=True, name="uq_username")

    db[SURVEYS].create_index("public_id", unique=True, name="uq_public_id")
    db[SURVEYS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_owner_created",
    )

    db[QUESTIONS].create_index(
        [("survey_id", ASCENDING), ("order", ASCENDING)],
        unique=True,
        name="uq_survey_order",
    )

    db[RESPONSES].create_index(
        [("survey_id", ASCENDING), ("status", ASCENDING)],
        name="ix_survey_status",
    )
    db[RESPONSES].create_index(
        [("survey_id", ASCENDING), ("respondent_id", ASCENDING), ("status", ASCENDING)],
        name="ix_survey_respondent_status",
    )
    db[RESPONSES].create_index(
        [("survey_id", ASCENDING), ("respondent_key", ASCENDING), ("status", ASCENDING)],
        name="ix_survey_key_status",
    )
    db[RESPONSES].create_index("answers.question_id", name="ix_answer_question")

    # 同一用户在同一问卷下最多一份“填写中”草稿
    db[RESPONSES].create_index(
        [("survey_id", ASCENDING), ("respondent_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "in_progress"},
        name="uq_in_progress_draft",
    )
