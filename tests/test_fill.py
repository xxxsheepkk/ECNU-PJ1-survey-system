from db import RESPONSES
from tests.helpers import (
    add_question,
    add_rule,
    answer,
    create_survey,
    current_draft,
    latest_submitted,
    publish,
    register,
    start,
    submit,
)


def test_single_choice_jump_skips_middle_questions(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "single", "第一题", options="A\nB")
    add_question(client, db, survey, "text", "第二题")
    add_question(client, db, survey, "text", "第三题")
    q4 = add_question(client, db, survey, "text", "第四题")
    add_rule(client, survey, q1, "equals", "a", q4["_id"])
    publish(client, survey)

    start(client, survey)
    draft = current_draft(db, survey)
    assert draft["current_question_id"] == q1["_id"]

    response = answer(client, survey, q1, "a")
    assert response.status_code == 302
    draft = current_draft(db, survey)
    assert draft["current_question_id"] == q4["_id"]

    answer(client, survey, q4, "完成")
    submit(client, survey)

    submitted = latest_submitted(db, survey)
    assert submitted["status"] == "submitted"
    assert submitted["path"] == [q1["_id"], q4["_id"]]
    assert [item["value"] for item in submitted["answers"]] == ["a", "完成"]


def test_multiple_choice_jump_by_contains(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "multiple", "第一题", options="A\nB\nC")
    add_question(client, db, survey, "text", "第二题")
    add_question(client, db, survey, "text", "第三题")
    q4 = add_question(client, db, survey, "text", "第四题")
    add_rule(client, survey, q1, "contains", "c", q4["_id"])
    publish(client, survey)

    start(client, survey)
    answer(client, survey, q1, ["a", "c"])
    draft = current_draft(db, survey)
    assert draft["current_question_id"] == q4["_id"]

    answer(client, survey, q4, "结束")
    submit(client, survey)
    submitted = latest_submitted(db, survey)
    assert submitted["path"] == [q1["_id"], q4["_id"]]


def test_number_jump_by_threshold(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(
        client, db, survey, "number", "年龄", min_value=0, max_value=120, integer_only=True
    )
    add_question(client, db, survey, "text", "第二题")
    q3 = add_question(client, db, survey, "text", "第三题")
    add_rule(client, survey, q1, "gte", 18, q3["_id"])
    publish(client, survey)

    start(client, survey)
    answer(client, survey, q1, "20")
    draft = current_draft(db, survey)
    assert draft["current_question_id"] == q3["_id"]


def test_draft_is_saved_after_each_answer_and_can_be_abandoned(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    q1 = add_question(client, db, survey, "text", "第一题")
    q2 = add_question(client, db, survey, "text", "第二题")
    publish(client, survey)

    start(client, survey)
    answer(client, survey, q1, "只答第一题")
    draft = current_draft(db, survey)
    assert len(draft["answers"]) == 1
    assert draft["path"] == [q1["_id"]]
    assert draft["current_question_id"] == q2["_id"]

    response = client.post(
        f"/survey/{survey['public_id']}/abandon", follow_redirects=True
    )
    assert "已放弃" in response.get_data(as_text=True)
    assert db[RESPONSES].count_documents(
        {"survey_id": survey["_id"], "status": "in_progress"}
    ) == 0


def test_anonymous_submission_removes_user_and_blocks_duplicate(client, db):
    register(client, "alice")
    survey = create_survey(client, db, allow_multiple="")
    q1 = add_question(client, db, survey, "text", "第一题", required=True)
    q2 = add_question(client, db, survey, "text", "第二题")
    publish(client, survey)

    start(client, survey, anonymous=True)
    answer(client, survey, q1, "匿名答案")
    answer(client, survey, q2, "第二题答案")
    submit(client, survey)

    submitted = latest_submitted(db, survey)
    assert submitted["anonymous"] is True
    assert "respondent_id" not in submitted
    assert submitted.get("respondent_key")

    response = client.post(
        f"/survey/{survey['public_id']}/start",
        data={"anonymous": "1"},
        follow_redirects=True,
    )
    assert "不允许重复提交" in response.get_data(as_text=True)
