"""应用入口：创建 Flask 应用、注册蓝图、CSRF 与模板过滤器。"""

import hmac
import os
import secrets

from flask import Flask, abort, g, render_template, request, session

import auth
import banks
import fill
import library
import stats
import surveys
from config import Config
from db import init_db
from utils import (
    QUESTION_TYPES,
    STATUS_LABELS,
    answer_to_text,
    fmt_dt,
    fmt_local_input,
    option_label,
    question_settings_summary,
    status_label,
)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    init_db(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(surveys.bp)
    app.register_blueprint(fill.bp)
    app.register_blueprint(stats.bp)
    app.register_blueprint(library.bp)
    app.register_blueprint(banks.bp)

    def csrf_token():
        token = session.get("_csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf"] = token
        return token

    @app.before_request
    def csrf_protect():
        if not app.config.get("CSRF_ENABLED", True):
            return
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.endpoint == "static":
                return
            expected = session.get("_csrf")
            submitted = request.form.get("_csrf")
            if (
                not expected
                or not submitted
                or not hmac.compare_digest(str(expected), str(submitted))
            ):
                abort(400, description="CSRF 校验失败，请刷新页面后重新提交。")

    @app.context_processor
    def inject_globals():
        return {
            "current_user": getattr(g, "user", None),
            "csrf_token": csrf_token,
            "QUESTION_TYPES": QUESTION_TYPES,
            "STATUS_LABELS": STATUS_LABELS,
        }

    app.add_template_filter(fmt_dt, "dt")
    app.add_template_filter(fmt_local_input, "dt_input")
    app.add_template_filter(
        lambda value, question: answer_to_text(question, value), "answer_text"
    )
    app.add_template_filter(question_settings_summary, "settings_summary")
    app.add_template_filter(
        lambda key, question: option_label(question, key), "option_label"
    )
    app.add_template_filter(status_label, "status_label")
    app.add_template_filter(lambda qtype: QUESTION_TYPES.get(qtype, qtype), "type_label")

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "errors/error.html",
                code=400,
                message=getattr(error, "description", "请求不合法。"),
            ),
            400,
        )

    @app.errorhandler(403)
    def forbidden(error):
        return (
            render_template("errors/error.html", code=403, message="没有权限执行该操作。"),
            403,
        )

    @app.errorhandler(404)
    def not_found(error):
        return (
            render_template(
                "errors/error.html", code=404, message="页面不存在，或你没有权限访问。"
            ),
            404,
        )

    @app.errorhandler(500)
    def server_error(error):
        return (
            render_template("errors/error.html", code=500, message="服务器内部错误。"),
            500,
        )

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=debug)
