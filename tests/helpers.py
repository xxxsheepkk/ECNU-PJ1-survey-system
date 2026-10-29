"""测试用 HTTP 流程辅助函数。"""

from bson import ObjectId

from db import RESPONSES, SURVEY_QUESTIONS, SURVEYS


def register(client, username="alice", password="secret123"):
    return client.post(
        "/register",
        data={
            "username": username,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=True,
    )


def login(client, username="alice", password="secret123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/logout", follow_redirects=True)


def create_survey(client, db, title="测试问卷", **overrides):
    data = {
        "title": title,
        "description": "用于测试的问卷",
        "allow_anonymous": "1",
        "allow_multiple": "1",
        "deadline": "",
    }
    data.update(overrides)
    response = client.post("/surveys/new", data=data, follow_redirects=False)
    assert response.status_code in (302, 303), response.get_data(as_text=True)
    survey_id = response.headers["Location"].rstrip("/").split("/")[-1]
    survey = db[SURVEYS].find_one({"_id": ObjectId(survey_id)})
    assert survey is not None
    return survey


def add_question(client, db, survey, qtype, title, **fields):
    data = {"type": qtype, "title": title}
    if fields.get("required"):
        data["required"] = "1"
    if qtype in ("single", "multiple"):
        data["options"] = fields.get("options", "选项A\n选项B")
    if qtype == "multiple":
        if "min_selections" in fields:
            data["min_selections"] = str(fields["min_selections"])
        if "max_selections" in fields:
            data["max_selections"] = str(fields["max_selections"])
    if qtype == "text":
        if "min_length" in fields:
            data["min_length"] = str(fields["min_length"])
        if "max_length" in fields:
            data["max_length"] = str(fields["max_length"])
    if qtype == "number":
        if "min_value" in fields:
            data["min_value"] = str(fields["min_value"])
        if "max_value" in fields:
            data["max_value"] = str(fields["max_value"])
        if fields.get("integer_only"):
            data["integer_only"] = "1"

    response = client.post(
        f"/surveys/{survey['_id']}/questions/new",
        data=data,
        follow_redirects=True,
    )
    assert response.status_code == 200
    question = db[SURVEY_QUESTIONS].find_one(
        {"survey_id": survey["_id"], "title": title}
    )
    assert question is not None, response.get_data(as_text=True)
    return question


def add_rule(client, survey, question, operator, value, target):
    response = client.post(
        f"/surveys/{survey['_id']}/questions/{question['_id']}/rules",
        data={"operator": operator, "value": str(value), "target": str(target)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    return response


def publish(client, survey):
    response = client.post(
        f"/surveys/{survey['_id']}/publish", follow_redirects=True
    )
    assert response.status_code == 200
    return response


def start(client, survey, anonymous=False):
    data = {}
    if anonymous:
        data["anonymous"] = "1"
    return client.post(
        f"/survey/{survey['public_id']}/start",
        data=data,
        follow_redirects=False,
    )


def current_draft(db, survey):
    return db[RESPONSES].find_one(
        {"survey_id": survey["_id"], "status": "in_progress"}
    )


def answer(client, survey, question, value):
    data = {"question_id": str(question["_id"]), "value": value}
    return client.post(
        f"/survey/{survey['public_id']}/fill/answer",
        data=data,
        follow_redirects=False,
    )


def submit(client, survey):
    return client.post(
        f"/survey/{survey['public_id']}/review/submit",
        follow_redirects=True,
    )


def latest_submitted(db, survey):
    return db[RESPONSES].find_one(
        {"survey_id": survey["_id"], "status": "submitted"},
        sort=[("submitted_at", -1)],
    )
