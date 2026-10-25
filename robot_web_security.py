"""Configuration and shared API authentication, without hardware side effects."""

import hmac
import ipaddress
import os
import re


def load_web_settings(environ=None):
    environ = os.environ if environ is None else environ
    token = environ.get("ROBOT_API_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise RuntimeError("Set ROBOT_API_TOKEN to a random 32-256 character URL-safe secret before starting the robot")
    host = environ.get("ROBOT_BIND_HOST", "127.0.0.1")
    try:
        ipaddress.ip_address(host)
    except ValueError as exc:
        raise RuntimeError("ROBOT_BIND_HOST must be an explicit IP address") from exc
    return host, token


def install_api_security(app, token):
    from flask import jsonify, request

    @app.before_request
    def authenticate_api():
        if not request.path.startswith("/api/"):
            return None
        scheme, separator, supplied = request.headers.get("Authorization", "").partition(" ")
        if (scheme.lower() != "bearer" or not separator or len(supplied) > 256
                or not hmac.compare_digest(supplied.encode("utf-8"), token.encode("ascii"))):
            response = jsonify(error="Robot API authentication required")
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response

    @app.after_request
    def prevent_api_caching(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
