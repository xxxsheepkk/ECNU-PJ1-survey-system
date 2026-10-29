from db import SURVEY_QUESTIONS
from tests.helpers import (
    add_question,
    create_survey,
    publish,
    register,
)


def test_create_four_question_types_and_publish(client, db):
    register(client, "alice")
    survey = create_survey(client, db)

    q1 = add_question(
        client, db, survey, "single", "你的性别？", options="男\n女\n其他", required=True
    )
    q2 = add_question(
        client,
        db,
        survey,
        "multiple",
        "你喜欢哪些水果？",
        options="苹果\n香蕉\n西瓜",
        min_selections=1,
        max_selections=2,
    )
    q3 = add_question(
        client,
        db,
        survey,
        "text",
        "请写一句说明",
        min_length=2,
        max_length=50,
    )
    q4 = add_question(
        client,
        db,
        survey,
        "number",
        "你的年龄？",
        min_value=0,
        max_value=120,
        integer_only=True,
    )

    assert q1["type"] == "single"
    assert len(q1["options"]) == 3
    assert q2["settings"] == {"min_selections": 1, "max_selections": 2}
    assert q3["settings"] == {"min_length": 2, "max_length": 50}
    assert q4["settings"] == {"min": 0.0, "max": 120.0, "integer_only": True}

    response = publish(client, survey)
    assert "问卷已发布" in response.get_data(as_text=True)

    # 发布后锁定结构：再添加题目应被拒绝
    response = client.post(
        f"/surveys/{survey['_id']}/questions/new",
        data={"type": "text", "title": "发布后新增"},
        follow_redirects=True,
    )
    assert "发布后不能增加题目" in response.get_data(as_text=True)
    count = db[SURVEY_QUESTIONS].count_documents({"survey_id": survey["_id"]})
    assert count == 4


def test_publish_requires_question(client, db):
    register(client, "alice")
    survey = create_survey(client, db)
    response = publish(client, survey)
    assert "至少添加一道题目" in response.get_data(as_text=True)
    assert db.surveys.find_one({"_id": survey["_id"]})["status"] == "draft"
