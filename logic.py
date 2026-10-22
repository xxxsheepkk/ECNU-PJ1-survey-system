"""问卷核心逻辑：答案校验、跳转规则匹配与规则校验。

跳转规则全部来自 questions.jump_rules，不在代码里写死某道题。
"""

import math

from utils import (
    COUNT_VALUE_OPERATORS,
    JUMP_OPERATORS,
    NUMBER_VALUE_OPERATORS,
    OPTION_VALUE_OPERATORS,
    option_label,
    to_object_id,
)


def normalize_answer(question, raw):
    """校验并规范化答案，返回 (value, error)。"""
    qtype = question["type"]
    required = bool(question.get("required"))
    settings = question.get("settings") or {}
    keys = [opt["key"] for opt in question.get("options", [])]

    if qtype == "single":
        value = "" if raw is None else str(raw).strip()
        if not value:
            return (None, "该题为必答题，请选择一个选项。") if required else (None, None)
        if value not in keys:
            return None, "选择的选项不存在，请重新选择。"
        return value, None

    if qtype == "multiple":
        selected = []
        for item in raw or []:
            item = str(item).strip()
            if item and item not in selected:
                selected.append(item)
        selected = [key for key in keys if key in selected]

        if not selected:
            return (
                (None, "该题为必答题，请至少选择一个选项。")
                if required
                else ([], None)
            )
        for key in selected:
            if key not in keys:
                return None, "包含不存在的选项，请重新选择。"

        low = settings.get("min_selections")
        high = settings.get("max_selections")
        if low is not None and len(selected) < low:
            return None, f"该题至少需要选择 {low} 项。"
        if high is not None and len(selected) > high:
            return None, f"该题最多只能选择 {high} 项。"
        return selected, None

    if qtype == "text":
        value = "" if raw is None else str(raw)
        if required and not value.strip():
            return None, "该题为必答题，请填写内容。"
        if value:
            low = settings.get("min_length")
            high = settings.get("max_length")
            if low is not None and len(value) < low:
                return None, f"至少需要输入 {low} 个字符。"
            if high is not None and len(value) > high:
                return None, f"最多只能输入 {high} 个字符。"
        return value, None

    if qtype == "number":
        value = "" if raw is None else str(raw).strip()
        if not value:
            return (None, "该题为必答题，请填写数字。") if required else (None, None)
        try:
            if settings.get("integer_only"):
                if any(ch in value for ch in (".", "e", "E")):
                    return None, "该题必须填写整数。"
                number = int(value)
            else:
                number = float(value)
        except (TypeError, ValueError):
            return None, "请填写合法的数字。"
        if isinstance(number, float) and not math.isfinite(number):
            return None, "请填写有限的数字。"
        low = settings.get("min")
        high = settings.get("max")
        if low is not None and number < low:
            return None, f"数值不能小于 {low}。"
        if high is not None and number > high:
            return None, f"数值不能大于 {high}。"
        return number, None

    return None, "未知的题型。"


def rule_matches(question, rule, value):
    condition = rule.get("condition") or {}
    operator = condition.get("operator")
    operand = condition.get("value")
    qtype = question["type"]

    if qtype == "single":
        if operator == "equals":
            return value == operand
        if operator == "not_equals":
            return value != operand
        return False

    if qtype == "multiple":
        selected = value or []
        if operator == "contains":
            return operand in selected
        if operator == "not_contains":
            return operand not in selected
        if operator == "count_gte":
            return len(selected) >= operand
        if operator == "count_lte":
            return len(selected) <= operand
        if operator == "count_eq":
            return len(selected) == operand
        return False

    if qtype == "number":
        if value is None:
            return False
        try:
            if operator == "lt":
                return value < operand
            if operator == "lte":
                return value <= operand
            if operator == "eq":
                return value == operand
            if operator == "gte":
                return value >= operand
            if operator == "gt":
                return value > operand
        except TypeError:
            return False
    return False


def evaluate_next_question(question, value, ordered_questions):
    """第一条命中的规则生效；否则按顺序进入下一题。"""
    for rule in question.get("jump_rules", []):
        if not rule_matches(question, rule, value):
            continue
        target = rule.get("target")
        if target == "end":
            return None
        for candidate in ordered_questions:
            if candidate["_id"] == target and candidate["order"] > question["order"]:
                return candidate
        # 目标异常时继续看下一条规则

    for candidate in ordered_questions:
        if candidate["order"] > question["order"]:
            return candidate
    return None


def validate_jump_rule(source, operator, raw_value, raw_target, questions):
    """校验并构造一条跳转规则，返回 (rule, error)。"""
    allowed = JUMP_OPERATORS.get(source["type"])
    if not allowed:
        return None, "该题型不支持设置跳转规则。"
    if operator not in allowed:
        return None, "跳转条件不合法。"

    if raw_target == "end":
        target = "end"
    else:
        target_id = to_object_id(raw_target)
        target_question = next((q for q in questions if q["_id"] == target_id), None)
        if target_question is None:
            return None, "跳转目标题目不存在。"
        if target_question["order"] <= source["order"]:
            return None, "只能跳转到后面的题目，以避免循环跳转。"
        target = target_id

    if operator in OPTION_VALUE_OPERATORS:
        keys = [opt["key"] for opt in source.get("options", [])]
        value = str(raw_value or "").strip()
        if value not in keys:
            return None, "请选择一个有效的选项作为条件。"
    elif operator in COUNT_VALUE_OPERATORS:
        try:
            value = int(str(raw_value).strip())
        except (TypeError, ValueError):
            return None, "选择数量条件必须是整数。"
        if value < 0:
            return None, "选择数量不能为负数。"
        if value > len(source.get("options", [])):
            return None, "选择数量条件不能超过选项总数。"
    elif operator in NUMBER_VALUE_OPERATORS:
        try:
            value = float(str(raw_value).strip())
        except (TypeError, ValueError):
            return None, "数值条件必须是数字。"
        if not math.isfinite(value):
            return None, "数值条件必须是有限数字。"
    else:
        return None, "未知的跳转条件。"

    return {"condition": {"operator": operator, "value": value}, "target": target}, None


def describe_rule(rule, source, questions_by_id):
    operator = rule["condition"]["operator"]
    value = rule["condition"]["value"]
    if operator in OPTION_VALUE_OPERATORS:
        value_text = option_label(source, value)
    else:
        value_text = str(value)

    target = rule.get("target")
    if target == "end":
        target_text = "结束问卷"
    else:
        target_question = questions_by_id.get(target)
        target_text = (
            f"第 {target_question['order']} 题" if target_question else "（目标缺失）"
        )
    return {
        "operator_label": JUMP_OPERATORS[source["type"]].get(operator, operator),
        "value_label": value_text,
        "target_label": target_text,
    }


def validate_all_rules(questions):
    """发布前检查所有规则是否仍有效。"""
    by_id = {q["_id"]: q for q in questions}
    errors = []
    for question in questions:
        for index, rule in enumerate(question.get("jump_rules", []), start=1):
            condition = rule.get("condition") or {}
            operator = condition.get("operator")
            target = rule.get("target")
            if operator not in JUMP_OPERATORS.get(question["type"], {}):
                errors.append(f"第 {question['order']} 题的第 {index} 条规则条件无效。")
                continue
            if target != "end":
                target_question = by_id.get(target)
                if target_question is None:
                    errors.append(f"第 {question['order']} 题的第 {index} 条规则目标不存在。")
                elif target_question["order"] <= question["order"]:
                    errors.append(f"第 {question['order']} 题的第 {index} 条规则目标不在后面。")
            if operator in COUNT_VALUE_OPERATORS:
                try:
                    if int(condition.get("value")) > len(question.get("options", [])):
                        errors.append(
                            f"第 {question['order']} 题的第 {index} 条规则数量超过选项总数。"
                        )
                except (TypeError, ValueError):
                    errors.append(f"第 {question['order']} 题的第 {index} 条规则数量无效。")
    return errors
