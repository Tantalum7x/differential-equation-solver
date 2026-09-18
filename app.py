"""
Flask 入口。
路由：
  GET  /         首页（输入表单）
  POST /preview  JSON 预览接口
  POST /solve    JSON 求解接口
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os

from flask import Flask, jsonify, has_request_context, render_template, request

from solver import SolveRequest, preview_expression, solve
from wolfram_session import get_session

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
log = logging.getLogger("app")
TOKEN_HEADER = "X-Access-Token"


def _as_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _access_token() -> str:
    return os.environ.get("ACCESS_TOKEN", "").strip()


def _access_token_enabled() -> bool:
    return bool(_access_token())


def _is_loopback_hostname(hostname: str) -> bool:
    host = (hostname or "").strip().lower().strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname_from_host_header(host_header: str) -> str:
    host = (host_header or "").strip()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":", 1)[0]


def _is_local_direct_request() -> bool:
    if not has_request_context():
        return False
    return _is_loopback_hostname(_hostname_from_host_header(request.host))


def _access_token_required_for_request() -> bool:
    return _access_token_enabled() and not _is_local_direct_request()


def _request_token(data: dict | None = None) -> str:
    if has_request_context():
        header_token = request.headers.get(TOKEN_HEADER, "")
        if header_token:
            return header_token.strip()
    if data:
        return _as_str(data.get("access_token", "")).strip()
    return ""


def _check_access_token(data: dict | None = None) -> bool:
    expected = _access_token()
    if not expected or _is_local_direct_request():
        return True
    return hmac.compare_digest(_request_token(data), expected)


def _access_denied_response():
    return jsonify({"ok": False, "error": "访问口令不正确或缺失"}), 401


@app.get("/")
def index():
    host = os.environ.get("HOST", "127.0.0.1")
    return render_template(
        "index.html",
        access_token_required=_access_token_required_for_request(),
        bind_host=host,
    )


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "font-src 'self' data: https://cdn.jsdelivr.net; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
    )
    return resp


@app.post("/preview")
def preview_route():
    """
    多行预览：把每行（非空）作为一条独立表达式做 normalize + TeXForm。
    入参：{text, fmt, functions, variables}（functions/variables 是逗号分隔字符串）
    返回：{lines: [{input, ok, latex, wolfram, error}]}
    """
    data = request.get_json(silent=True) or {}
    if not _check_access_token(data):
        return _access_denied_response()

    text = _as_str(data.get("text", ""))
    fmt = _as_str(data.get("fmt", "wolfram")) or "wolfram"
    fns = [p.strip() for p in _as_str(data.get("functions", "")).split(",") if p.strip()]
    vars_ = [p.strip() for p in _as_str(data.get("variables", "")).split(",") if p.strip()]

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines:
        result = preview_expression(ln, fmt, fns, vars_)
        result["input"] = ln
        out.append(result)
    return jsonify({"lines": out})


@app.post("/solve")
def solve_route():
    data = request.get_json(silent=True) or {}
    if not _check_access_token(data):
        return _access_denied_response()

    def _split_lines(s) -> list[str]:
        return [line.strip() for line in _as_str(s).splitlines() if line.strip()]

    def _split_csv(s) -> list[str]:
        return [p.strip() for p in _as_str(s).split(",") if p.strip()]

    req = SolveRequest(
        fmt=_as_str(data.get("fmt", "wolfram")) or "wolfram",
        kind=_as_str(data.get("kind", "ode")) or "ode",
        mode=_as_str(data.get("mode", "analytic")) or "analytic",
        equations=_split_lines(data.get("equations", "")),
        functions=_split_csv(data.get("functions", "")),
        variables=_split_csv(data.get("variables", "")),
        conditions=_split_lines(data.get("conditions", "")),
        plot_range=_as_str(data.get("plot_range", "")).strip(),
        plot=bool(data.get("plot", False)),
    )
    result = solve(req)
    return jsonify(result.to_dict())


def _warmup():
    """Flask 启动时预热 kernel，避开第一次请求时的 2–4s 延迟。"""
    try:
        get_session().evaluate("1+1")
        log.info("Wolfram kernel warmed up")
    except Exception as e:
        log.warning("Wolfram kernel warmup failed: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _warmup()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5001"))
    log.info("Starting Flask server on %s:%s", host, port)
    if host not in {"127.0.0.1", "localhost"} and not _access_token_enabled():
        log.warning("Remote access is enabled without ACCESS_TOKEN. Use only on trusted networks.")
    app.run(host=host, port=port, debug=False)
