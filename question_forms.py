"""题目内容表单解析与回显（题目库与问卷编辑共用）。"""

from flask import request

from utils import (
    QUESTION_TYPES,
    parse_optional_float,
    parse_optional_int,
)


def next_option_key(used):
    for index in range(26):
        key = chr(ord("a") + index)
        if key not in used:
            return key
    return f"o{len(used) + 1}"


def parse_question_form(existing=None):
    """解析题目内容表单；编辑时题型保持不变。返回 (fields, errors)。"""
    qtype = request.form.get("type") if existing is None else existing["type"]
    if qtype not in QUESTION_TYPES:
        return None, ["请选择合法的题型。"]

    title = request.form.get("title", "").strip()
    errors = []
    if not title:
        errors.append("请填写题目内容。")
    elif len(title) > 500:
        errors.append("题目内容最多 500 个字符。")
    required = bool(request.form.get("required"))

    options = []
    settings = {}

    if qtype in ("single", "multiple"):
        lines = [
            line.strip()
            for line in request.form.get("options", "").splitlines()
            if line.strip()
        ]
        if len(lines) < 2:
            errors.append("选择题至少需要 2 个选项。")
        elif len(lines) > 20:
            errors.append("选择题最多支持 20 个选项。")
        elif len(set(lines)) != len(lines):
            errors.append("选项内容不能重复。")

        old_key_by_text = {
            opt["text"]: opt["key"] for opt in (existing or {}).get("options", [])
        }
        used = set()
        for text in lines:
            key = old_key_by_text.get(text)
            if key is None or key in used:
                key = next_option_key(used)
            used.add(key)
            options.append({"key": key, "text": text})

        if qtype == "multiple":
            low, low_error = parse_optional_int(request.form.get("min_selections"))
            high, high_error = parse_optional_int(request.form.get("max_selections"))
            if low_error:
                errors.append("最少选择数量" + low_error + "。")
            if high_error:
                errors.append("最多选择数量" + high_error + "。")
            if low is not None and low < 1:
                errors.append("最少选择数量必须大于等于 1。")
            if high is not None and high < 1:
                errors.append("最多选择数量必须大于等于 1。")
            if low is not None and high is not None and low > high:
                errors.append("最少选择数量不能大于最多选择数量。")
            if high is not None and len(options) and high > len(options):
                errors.append("最多选择数量不能超过选项总数。")
            if low is not None and len(options) and low > len(options):
                errors.append("最少选择数量不能超过选项总数。")
            settings = {"min_selections": low, "max_selections": high}

    elif qtype == "text":
        low, low_error = parse_optional_int(request.form.get("min_length"))
        high, high_error = parse_optional_int(request.form.get("max_length"))
        if low_error:
            errors.append("最少字数" + low_error + "。")
        if high_error:
            errors.append("最多字数" + high_error + "。")
        if low is not None and low < 0:
            errors.append("最少字数不能为负数。")
        if high is not None and high < 0:
            errors.append("最多字数不能为负数。")
        if low is not None and high is not None and low > high:
            errors.append("最少字数不能大于最多字数。")
        if high is not None and high > 5000:
            errors.append("最多字数不能超过 5000。")
        settings = {"min_length": low, "max_length": high}

    elif qtype == "number":
        low, low_error = parse_optional_float(request.form.get("min_value"))
        high, high_error = parse_optional_float(request.form.get("max_value"))
        if low_error:
            errors.append("最小值" + low_error + "。")
        if high_error:
            errors.append("最大值" + high_error + "。")
        if low is not None and high is not None and low > high:
            errors.append("最小值不能大于最大值。")
        settings = {
            "min": low,
            "max": high,
            "integer_only": bool(request.form.get("integer_only")),
        }

    return {
        "type": qtype,
        "title": title,
        "required": required,
        "options": options,
        "settings": settings,
    }, errors


def question_values(question=None):
    if question is None:
        return {
            "type": "single",
            "title": "",
            "required": False,
            "options": "",
            "min_selections": "",
            "max_selections": "",
            "min_length": "",
            "max_length": "",
            "min_value": "",
            "max_value": "",
            "integer_only": False,
        }
    settings = question.get("settings") or {}
    return {
        "type": question["type"],
        "title": question["title"],
        "required": question.get("required", False),
        "options": "\n".join(opt["text"] for opt in question.get("options", [])),
        "min_selections": display_number(settings.get("min_selections")),
        "max_selections": display_number(settings.get("max_selections")),
        "min_length": display_number(settings.get("min_length")),
        "max_length": display_number(settings.get("max_length")),
        "min_value": display_number(settings.get("min")),
        "max_value": display_number(settings.get("max")),
        "integer_only": bool(settings.get("integer_only")),
    }


def form_question_values():
    return {
        "type": request.form.get("type", "single"),
        "title": request.form.get("title", ""),
        "required": bool(request.form.get("required")),
        "options": request.form.get("options", ""),
        "min_selections": request.form.get("min_selections", ""),
        "max_selections": request.form.get("max_selections", ""),
        "min_length": request.form.get("min_length", ""),
        "max_length": request.form.get("max_length", ""),
        "min_value": request.form.get("min_value", ""),
        "max_value": request.form.get("max_value", ""),
        "integer_only": bool(request.form.get("integer_only")),
    }


def display_number(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
