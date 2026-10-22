"""通用工具：时间、ID、数字解析、展示格式、匿名去重。"""

import hashlib
import hmac
import math
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

STATUS_LABELS = {
    "draft": "草稿",
    "published": "已发布",
    "closed": "已关闭",
}

QUESTION_TYPES = {
    "single": "单选题",
    "multiple": "多选题",
    "text": "文本填空题",
    "number": "数字填空题",
}

JUMP_OPERATORS = {
    "single": {
        "equals": "等于选项",
        "not_equals": "不等于选项",
    },
    "multiple": {
        "contains": "包含选项",
        "not_contains": "不包含选项",
        "count_gte": "选择数量 ≥",
        "count_lte": "选择数量 ≤",
        "count_eq": "选择数量 =",
    },
    "number": {
        "lt": "小于",
        "lte": "小于等于",
        "eq": "等于",
        "gte": "大于等于",
        "gt": "大于",
    },
}

OPTION_VALUE_OPERATORS = {"equals", "not_equals", "contains", "not_contains"}
COUNT_VALUE_OPERATORS = {"count_gte", "count_lte", "count_eq"}
NUMBER_VALUE_OPERATORS = {"lt", "lte", "eq", "gte", "gt"}


def utcnow():
    """无时区 UTC 时间，方便直接写 MongoDB。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_object_id(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError, ValueError):
        return None


def parse_optional_int(raw):
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        return int(str(raw).strip()), None
    except (TypeError, ValueError):
        return None, "必须是整数"


def parse_optional_float(raw):
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None, "必须是数字"
    if not math.isfinite(value):
        return None, "必须是有限数字"
    return value, None


def parse_local_datetime(raw):
    """解析 datetime-local，按服务器本地时区转成 UTC。"""
    if not raw or not str(raw).strip():
        return None, None
    try:
        value = datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None, "时间格式不正确"
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).replace(tzinfo=None), None


def fmt_dt(value, fmt="%Y-%m-%d %H:%M"):
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


def fmt_local_input(value):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%dT%H:%M")


def is_survey_open(survey):
    if survey["status"] == "draft":
        return False, "问卷尚未发布。"
    if survey["status"] == "closed":
        return False, "问卷已关闭。"
    deadline = survey.get("deadline")
    if deadline is not None and utcnow() > deadline:
        return False, "问卷已超过截止时间。"
    return True, None


def status_label(survey):
    if (
        survey["status"] == "published"
        and survey.get("deadline")
        and utcnow() > survey["deadline"]
    ):
        return "已截止"
    return STATUS_LABELS.get(survey["status"], survey["status"])


def option_map(question):
    return {opt["key"]: opt["text"] for opt in question.get("options", [])}


def option_label(question, key):
    return option_map(question).get(key, key)


def answer_to_text(question, value):
    if value is None:
        return "（未作答）"
    qtype = question["type"]
    if qtype == "single":
        return option_label(question, value)
    if qtype == "multiple":
        labels = [option_label(question, key) for key in value]
        return "、".join(labels) if labels else "（未选择）"
    if qtype == "number" and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def respondent_key(secret_key, user_id, survey_id):
    """匿名答卷的不可逆去重标识。"""
    message = f"{user_id}:{survey_id}".encode("utf-8")
    return hmac.new(str(secret_key).encode("utf-8"), message, hashlib.sha256).hexdigest()


def safe_redirect_target(target):
    if not target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def question_settings_summary(question):
    qtype = question["type"]
    settings = question.get("settings") or {}
    parts = []
    if qtype == "multiple":
        low = settings.get("min_selections")
        high = settings.get("max_selections")
        if low is not None and high is not None and low == high:
            parts.append(f"必须选择 {low} 项")
        else:
            if low is not None:
                parts.append(f"至少选择 {low} 项")
            if high is not None:
                parts.append(f"最多选择 {high} 项")
    elif qtype == "text":
        low = settings.get("min_length")
        high = settings.get("max_length")
        if low is not None:
            parts.append(f"最少 {low} 字")
        if high is not None:
            parts.append(f"最多 {high} 字")
    elif qtype == "number":
        low = settings.get("min")
        high = settings.get("max")
        if low is not None:
            parts.append(f"最小值 {low}")
        if high is not None:
            parts.append(f"最大值 {high}")
        if settings.get("integer_only"):
            parts.append("必须为整数")
    return "；".join(parts)
