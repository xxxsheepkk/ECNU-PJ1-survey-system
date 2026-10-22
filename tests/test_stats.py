from tests.helpers import (
    add_question,
    answer,
    create_survey,
    login,
    logout,
    publish,
    register,
    start,
    submit,
)


def _build_survey_and_responses(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "single", "单选", options="A\nB\nC")
    q2 = add_question(client, db, survey, "multiple", "多选", options="A\nB\nC")
    q3 = add_question(client, db, survey, "text", "文本")
    q4 = add_question(
        client, db, survey, "number", "数字", min_value=0, max_value=120
    )
    publish(client, survey)

    logout(client)
    register(client, "bob")
    start(client, survey)
    answer(client, survey, q1, "a")
    answer(client, survey, q2, ["a", "b"])
    answer(client, survey, q3, "hello")
    answer(client, survey, q4, "20")
    submit(client, survey)

    logout(client)
    register(client, "carol")
    start(client, survey)
    answer(client, survey, q1, "b")
    answer(client, survey, q2, ["b", "c"])
    answer(client, survey, q3, "world")
    answer(client, survey, q4, "30")
    submit(client, survey)

    return survey, (q1, q2, q3, q4)


def test_stats_aggregation_and_permissions(client, db):
    survey, (q1, q2, q3, q4) = _build_survey_and_responses(client, db)

    # carol 不是创建者，不能看统计
    response = client.get(f"/surveys/{survey['_id']}/stats")
    assert response.status_code == 404

    logout(client)
    login(client, "alice")
    response = client.get(f"/surveys/{survey['_id']}/stats")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "已提交 <strong>2</strong> 份" in text
    assert "hello" in text and "world" in text
    assert "25.0" in text
    assert "100.0%" in text  # 多选 B 被两个人选中

    detail = client.get(
        f"/surveys/{survey['_id']}/stats/questions/{q1['_id']}"
    ).get_data(as_text=True)
    assert "1（50.0%）" in detail
    assert "占比分母为实际回答该题的人数" in detail


def test_text_answers_are_paginated(client, db, app):
    survey, (_, _, q3, _) = _build_survey_and_responses(client, db)
    logout(client)
    login(client, "alice")

    app.config["STATS_ANSWER_LIMIT"] = 1
    url = f"/surveys/{survey['_id']}/stats/questions/{q3['_id']}"
    first = client.get(url)
    assert first.status_code == 200
    first_text = first.get_data(as_text=True)
    assert "共 2 条" in first_text
    assert "第 1 / 2 页" in first_text
    assert ("hello" in first_text) != ("world" in first_text)

    second = client.get(url + "?page=2")
    second_text = second.get_data(as_text=True)
    assert "第 2 / 2 页" in second_text
    assert ("hello" in second_text) != ("world" in second_text)
    assert "hello" in first_text + second_text
    assert "world" in first_text + second_text

    clamped = client.get(url + "?page=999")
    assert "第 2 / 2 页" in clamped.get_data(as_text=True)


def test_drafts_do_not_count_as_submissions(client, db):
    survey, (q1, _, _, _) = _build_survey_and_responses(client, db)

    # bob 已提交过一次；允许重复提交，再开一份草稿但不提交
    logout(client)
    login(client, "bob")
    start(client, survey)
    answer(client, survey, q1, "a")

    logout(client)
    login(client, "alice")
    response = client.get(f"/surveys/{survey['_id']}/stats")
    assert "已提交 <strong>2</strong> 份" in response.get_data(as_text=True)
