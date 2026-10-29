"""MongoDB 连接与索引初始化。

集合划分（第二阶段）：
- users：账号；
- surveys：问卷元信息与公开链接；
- questions：题目族（全局题库，可被多问卷复用）；
- question_versions：题目版本（不可变内容快照，含 opt_id）；
- survey_questions：问卷对题目的引用（指向某版本 + 冻结快照 + 跳转规则）；
- question_shares：题目共享关系；
- question_banks / question_bank_items：题库；
- responses：答卷（答案冗余题目族 / 版本，支持跨问卷统计）。
"""

from flask import current_app, g
from pymongo import ASCENDING, DESCENDING, MongoClient

USERS = "users"
SURVEYS = "surveys"
QUESTIONS = "questions"
QUESTION_VERSIONS = "question_versions"
SURVEY_QUESTIONS = "survey_questions"
QUESTION_SHARES = "question_shares"
QUESTION_BANKS = "question_banks"
QUESTION_BANK_ITEMS = "question_bank_items"
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
    """创建索引（幂等）。"""
    db[USERS].create_index("username", unique=True, name="uq_username")

    db[SURVEYS].create_index("public_id", unique=True, name="uq_public_id")
    db[SURVEYS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_owner_created",
    )

    # 题目族：按创建者浏览；按当前版本定位
    db[QUESTIONS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_question_owner_created",
    )
    db[QUESTIONS].create_index("current_version_id", name="ix_question_current_version")

    # 题目版本：族内版本号唯一
    db[QUESTION_VERSIONS].create_index(
        [("question_id", ASCENDING), ("version_no", ASCENDING)],
        unique=True,
        name="uq_version_no",
    )

    # 问卷题目引用：同卷同族唯一；同卷顺序唯一；反查“题目被哪些问卷使用”
    db[SURVEY_QUESTIONS].create_index(
        [("survey_id", ASCENDING), ("question_id", ASCENDING)],
        unique=True,
        name="uq_survey_question",
    )
    db[SURVEY_QUESTIONS].create_index(
        [("survey_id", ASCENDING), ("order", ASCENDING)],
        unique=True,
        name="uq_survey_order",
    )
    db[SURVEY_QUESTIONS].create_index(
        [("question_id", ASCENDING)], name="ix_sq_question"
    )

    # 共享：同一题目对同一用户唯一；按被共享方查询
    db[QUESTION_SHARES].create_index(
        [("question_id", ASCENDING), ("grantee_user_id", ASCENDING)],
        unique=True,
        name="uq_share_question_grantee",
    )
    db[QUESTION_SHARES].create_index(
        [("grantee_user_id", ASCENDING), ("status", ASCENDING)],
        name="ix_share_grantee_status",
    )

    # 题库：按所有者浏览；条目在题库内唯一
    db[QUESTION_BANKS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_bank_owner_created",
    )
    db[QUESTION_BANK_ITEMS].create_index(
        [("question_bank_id", ASCENDING), ("question_id", ASCENDING)],
        unique=True,
        name="uq_bank_item_question",
    )
    db[QUESTION_BANK_ITEMS].create_index(
        [("question_bank_id", ASCENDING)], name="ix_bank_item_bank"
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
    # 跨问卷统计：按题目族聚合
    db[RESPONSES].create_index("answers.question_id", name="ix_answer_family")
    db[RESPONSES].create_index(
        "answers.survey_question_id", name="ix_answer_survey_question"
    )

    # 同一用户在同一问卷下最多一份“填写中”草稿
    db[RESPONSES].create_index(
        [("survey_id", ASCENDING), ("respondent_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "in_progress"},
        name="uq_in_progress_draft",
    )
