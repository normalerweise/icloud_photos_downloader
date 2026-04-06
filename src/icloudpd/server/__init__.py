import os
import sys
from logging import Logger

import waitress
from flask import Flask, Response, make_response, render_template, request

from icloudpd.status import Status, StatusExchange
from icloudpd.sync.schedule import SyncRunKind


def serve_app(logger: Logger, _status_exchange: StatusExchange) -> None:
    app = Flask(__name__)
    app.logger = logger
    # for running in pyinstaller
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir is not None:
        app.template_folder = os.path.join(bundle_dir, "templates")
        app.static_folder = os.path.join(bundle_dir, "static")

    @app.route("/")
    def index() -> Response | str:
        return render_template("index.html")

    @app.route("/status", methods=["GET"])
    def get_status() -> Response | str:
        _status = _status_exchange.get_status()
        _global_config = _status_exchange.get_global_config()
        _user_configs = _status_exchange.get_user_configs()
        _current_user = _status_exchange.get_current_user()
        _progress = _status_exchange.get_progress()
        _error = _status_exchange.get_error()

        if _status == Status.NO_INPUT_NEEDED:
            _log_entries = _status_exchange.get_log_buffer().get_all()
            _schedule_info = _status_exchange.get_schedule_info()
            return render_template(
                "no_input.html",
                status=_status,
                error=_error,
                progress=_progress,
                global_config=vars(_global_config) if _global_config else None,
                user_configs=[vars(uc) for uc in _user_configs] if _user_configs else [],
                current_user=_current_user,
                log_entries=_log_entries,
                schedule_info=_schedule_info,
            )
        if _status == Status.NEED_MFA:
            _trusted_devices = _status_exchange.get_trusted_devices()
            _sms_sent_device_id = _status_exchange.get_sms_sent_device_id()
            return render_template(
                "code.html",
                error=_error,
                current_user=_current_user,
                trusted_devices=_trusted_devices,
                sms_sent_device_id=_sms_sent_device_id,
            )
        if _status == Status.NEED_PASSWORD:
            return render_template("password.html", error=_error, current_user=_current_user)
        return render_template("status.html", status=_status)

    @app.route("/code", methods=["POST"])
    def set_code() -> Response | str:
        _current_user = _status_exchange.get_current_user()
        code = request.form.get("code")
        if code is not None:
            if _status_exchange.set_payload(code):
                return render_template("code_submitted.html", current_user=_current_user)
        else:
            logger.error(f"cannot find code in request {request.form}")
        return make_response(
            render_template(
                "auth_error.html",
                type="Two-Factor Code",
                current_user=_current_user,
            ),
            400,
        )  # incorrect code

    @app.route("/password", methods=["POST"])
    def set_password() -> Response | str:
        _current_user = _status_exchange.get_current_user()
        password = request.form.get("password")
        if password is not None:
            if _status_exchange.set_payload(password):
                return render_template("password_submitted.html", current_user=_current_user)
        else:
            logger.error(f"cannot find password in request {request.form}")
        return make_response(
            render_template("auth_error.html", type="password", current_user=_current_user),
            400,
        )  # incorrect code

    @app.route("/send-sms", methods=["POST"])
    def send_sms() -> Response | str:
        _current_user = _status_exchange.get_current_user()
        device_id_str = request.form.get("device_id")
        if device_id_str is not None:
            try:
                device_id = int(device_id_str)
            except ValueError:
                return make_response("Invalid device ID", 400)
            if _status_exchange.request_sms(device_id):
                return render_template(
                    "sms_requested.html", current_user=_current_user
                )
        return make_response(
            render_template(
                "auth_error.html",
                type="SMS request",
                current_user=_current_user,
            ),
            400,
        )

    @app.route("/trigger-sync", methods=["POST"])
    def trigger_sync() -> Response | str:
        username = request.form.get("username")
        kind_str = request.form.get("kind")
        if username is None or kind_str not in ("daily", "weekly"):
            return make_response("Bad request: username and kind (daily/weekly) required", 400)
        kind = SyncRunKind.DAILY if kind_str == "daily" else SyncRunKind.WEEKLY
        _status_exchange.trigger_sync(username, kind)
        _status_exchange.get_progress().resume = True
        return make_response("Ok", 200)

    @app.route("/resume", methods=["POST"])
    def resume() -> Response | str:
        _status_exchange.get_progress().resume = True
        return make_response("Ok", 200)

    @app.route("/cancel", methods=["POST"])
    def cancel() -> Response | str:
        _status_exchange.get_progress().cancel = True
        return make_response("Ok", 200)

    logger.debug("Starting web server...")
    return waitress.serve(app)
