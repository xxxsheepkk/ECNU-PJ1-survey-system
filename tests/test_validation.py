from db import QUESTIONS
from tests.helpers import (
    add_question,
    answer,
    create_survey,
    publish,
    register,
    start,
)


def test_required_single_and_invalid_option(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "single", "必答单选", options="A\nB", required=True)
    publish(client, survey)
    start(client, survey)

    response = answer(client, survey, q1, "")
    assert response.status_code == 400
    assert "必答题" in response.get_data(as_text=True)

    response = answer(client, survey, q1, "z")
    assert response.status_code == 400
    assert "选项不存在" in response.get_data(as_text=True)

    response = answer(client, survey, q1, "a")
    assert response.status_code == 302


def test_multiple_min_selections_and_option_count_config(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(
        client,
        db,
        survey,
        "multiple",
        "多选",
        options="A\nB\nC",
        min_selections=2,
        max_selections=3,
    )

    # 题目配置：最少选择数不能超过选项总数（发布前校验）
    response = client.post(
        f"/surveys/{survey['_id']}/questions/new",
        data={
            "type": "multiple",
            "title": "非法多选",
            "options": "A\nB\nC",
            "min_selections": "5",
        },
        follow_redirects=True,
    )
    assert "最少选择数量不能超过选项总数" in response.get_data(as_text=True)
    assert db[QUESTIONS].count_documents({"title": "非法多选"}) == 0

    publish(client, survey)
    start(client, survey)

    response = answer(client, survey, q1, ["a"])
    assert response.status_code == 400
    assert "至少需要选择 2 项" in response.get_data(as_text=True)

    response = answer(client, survey, q1, ["a", "b"])
    assert response.status_code == 302


def test_text_length_validation(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(
        client, db, survey, "text", "文本题", min_length=3, max_length=5, required=True
    )
    publish(client, survey)
    start(client, survey)

    assert answer(client, survey, q1, "ab").status_code == 400
    assert "至少需要输入 3 个字符" in answer(client, survey, q1, "ab").get_data(as_text=True)

    assert answer(client, survey, q1, "abcdef").status_code == 400
    assert "最多只能输入 5 个字符" in answer(client, survey, q1, "abcdef").get_data(as_text=True)

    assert answer(client, survey, q1, "abc").status_code == 302


def test_number_range_and_integer_validation(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(
        client,
        db,
        survey,
        "number",
        "年龄",
        min_value=0,
        max_value=120,
        integer_only=True,
        required=True,
    )
    publish(client, survey)
    start(client, survey)

    for value, message in [
        ("abc", "合法的数字"),
        ("12.5", "必须填写整数"),
        ("-1", "不能小于 0"),
        ("121", "不能大于 120"),
    ]:
        response = answer(client, survey, q1, value)
        assert response.status_code == 400
        assert message in response.get_data(as_text=True)

    assert answer(client, survey, q1, "30").status_code == 302


def test_invalid_jump_rule_is_rejected(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "single", "第一题", options="A\nB")
    add_question(client, db, survey, "text", "第二题")

    response = client.post(
        f"/surveys/{survey['_id']}/questions/{q1['_id']}/rules",
        data={"operator": "equals", "value": "z", "target": "end"},
        follow_redirects=True,
    )
    assert "有效的选项" in response.get_data(as_text=True)

    response = client.post(
        f"/surveys/{survey['_id']}/questions/{q1['_id']}/rules",
        data={"operator": "equals", "value": "a", "target": str(q1["_id"])},
        follow_redirects=True,
    )
    assert "只能跳转到后面的题目" in response.get_data(as_text=True)
