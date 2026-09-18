"""
微分方程求解核心。封装 wolframclient 调用。

输入有两个轴：
  fmt   : 'wolfram' | 'latex' | 'auto'        —— 数学表达式书写方式
  kind  : 'ode' | 'ode_system' | 'pde'        —— 方程类型
  mode  : 'analytic' | 'numeric'              —— 求 DSolve 还是 NDSolve

normalize_input 负责把每条输入字符串转成 Wolfram 表达式字符串。
solve 拼装 DSolve / NDSolve 调用并把解析解转 LaTeX、把数值解画图（PNG/base64）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from wolframclient.exception import WolframLanguageException
from wolframclient.language import wlexpr

from wolfram_session import get_eval_lock, get_session


MAX_INPUT_LEN = 4000  # 单字段字符上限，防误粘贴
MAX_LIST_ITEMS = 20
PREVIEW_TIMEOUT = 8
SOLVE_TIMEOUT = 25
PLOT_TIMEOUT = 35

IDENT_RE = re.compile(r"^[A-Za-z]\w*$")
RANGE_RE = re.compile(r"^\s*(\{\s*[A-Za-z]\w*\s*,\s*[^{};]+\s*,\s*[^{};]+\s*\}\s*)(,\s*\{\s*[A-Za-z]\w*\s*,\s*[^{};]+\s*,\s*[^{};]+\s*\}\s*)?$")
DANGEROUS_WL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])("
    r"Run|RunProcess|StartProcess|KillProcess|ExternalEvaluate|"
    r"ToExpression|MakeExpression|"
    r"Get|Put|PutAppend|Read|ReadString|ReadList|Write|WriteString|"
    r"Import|Export|URLRead|URLExecute|URLFetch|SystemOpen|"
    r"CloudEvaluate|SocketConnect|SocketListen|LibraryFunctionLoad|"
    r"CreateFile|DeleteFile|CopyFile|RenameFile|CreateDirectory|DeleteDirectory|SetDirectory|"
    r"Needs|Get|Install|Uninstall|LinkLaunch|PacletInstall|NotebookEvaluate"
    r")(?![A-Za-z0-9_$])"
)

LATEX_HINTS = (
    "\\frac", "\\sin", "\\cos", "\\tan", "\\log", "\\ln", "\\exp",
    "\\sqrt", "\\partial", "\\sum", "\\int", "\\pi", "\\alpha",
    "\\beta", "\\gamma", "\\theta", "\\lambda", "\\mu", "\\sigma",
)


def _validate_ident_list(name: str, items: list[str], *, required: bool = False) -> None:
    if required and not items:
        raise ValueError(f"{name} 不能为空")
    if len(items) > MAX_LIST_ITEMS:
        raise ValueError(f"{name} 数量过多，最多 {MAX_LIST_ITEMS} 个")
    for item in items:
        if not IDENT_RE.fullmatch(item):
            raise ValueError(f"{name} 只能包含字母、数字、下划线，且必须以字母开头：{item}")


def _validate_request(req: SolveRequest) -> None:
    if req.fmt not in {"wolfram", "latex", "auto"}:
        raise ValueError("未知输入格式")
    if req.kind not in {"ode", "ode_system", "pde"}:
        raise ValueError("未知方程类型")
    if req.mode not in {"analytic", "numeric"}:
        raise ValueError("未知求解模式")
    _validate_ident_list("未知函数", req.functions, required=True)
    _validate_ident_list("自变量", req.variables, required=True)
    if req.plot_range:
        _validate_safe_wolfram(req.plot_range, "绘图区间")
        if not RANGE_RE.fullmatch(req.plot_range):
            raise ValueError("绘图区间格式应为 {x, 0, 10} 或 {x, 0, Pi}, {t, 0, 1}")
    for text in req.equations:
        if text and _resolve_fmt(text, req.fmt) == "wolfram":
            _validate_safe_wolfram(text, "方程")
            if _BARE_EQ.search(text):
                raise ValueError(
                    f"Wolfram 表达式里方程应当用 == 表示等式（你写的是 =，会被解释为赋值）：{text}"
                )
    for text in req.conditions:
        if text and _resolve_fmt(text, req.fmt) == "wolfram":
            _validate_safe_wolfram(text, "条件")
            if _BARE_EQ.search(text):
                raise ValueError(
                    f"Wolfram 表达式里方程应当用 == 表示等式（你写的是 =，会被解释为赋值）：{text}"
                )


def _validate_safe_wolfram(text: str, label: str = "表达式") -> None:
    if len(text) > MAX_INPUT_LEN:
        raise ValueError(f"{label} 超过 {MAX_INPUT_LEN} 字符")
    if ";" in text:
        raise ValueError(f"{label} 不允许使用分号或复合表达式")
    if "<<" in text or ">>" in text:
        raise ValueError(f"{label} 不允许使用文件读写重定向语法")
    m = DANGEROUS_WL_RE.search(text)
    if m:
        raise ValueError(f"{label} 包含不允许的 Wolfram 调用：{m.group(1)}")


@dataclass
class SolveRequest:
    fmt: str = "wolfram"          # wolfram | latex | auto
    kind: str = "ode"             # ode | ode_system | pde
    mode: str = "analytic"        # analytic | numeric
    equations: list[str] = field(default_factory=list)  # 一行一条
    functions: list[str] = field(default_factory=list)  # ['y'] / ['x','y']
    variables: list[str] = field(default_factory=list)  # ['x'] / ['x','t']
    conditions: list[str] = field(default_factory=list)  # ['y[0]==1', ...]
    plot_range: str = ""          # '{x, 0, 10}' 或 '{x,0,Pi}, {t,0,1}'
    plot: bool = False


@dataclass
class SolveResult:
    ok: bool
    latex: str = ""               # 解析解 LaTeX
    raw: str = ""                 # InputForm 字符串
    image_base64: str = ""        # PNG base64
    normalized_eqs: list[str] = field(default_factory=list)
    normalized_conds: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latex": self.latex,
            "raw": self.raw,
            "image_base64": self.image_base64,
            "normalized_eqs": self.normalized_eqs,
            "normalized_conds": self.normalized_conds,
            "warnings": self.warnings,
            "error": self.error,
        }


# ------------------------------------------------------------------
# 输入规范化
# ------------------------------------------------------------------

def _looks_like_latex(s: str) -> bool:
    if "\\" in s:
        return True
    return any(h in s for h in LATEX_HINTS)


def _resolve_fmt(s: str, fmt: str) -> str:
    if fmt == "auto":
        return "latex" if _looks_like_latex(s) else "wolfram"
    return fmt


_LATEX_FRAC_DN = re.compile(
    r"\\frac\s*\{\s*d\s*\^\s*\{?\s*(\d+)\s*\}?\s*([a-zA-Z]\w*)\s*\}"
    r"\s*\{\s*d\s*([a-zA-Z]\w*)\s*\^\s*\{?\s*\d+\s*\}?\s*\}"
)
_LATEX_FRAC_D1 = re.compile(
    r"\\frac\s*\{\s*d\s*([a-zA-Z]\w*)\s*\}\s*\{\s*d\s*([a-zA-Z]\w*)\s*\}"
)


def _preprocess_latex_derivatives(s: str) -> str:
    """
    把 Leibniz 风格导数改写成带撇形式（带撇的 LaTeX 能被 TeXForm 正确解析为 Derivative[n][f][x]）：
      \\frac{d^n y}{dx^n}  ->  y''..(x)   (n 个撇，n>3 时退化为 y^{(n)}(x))
      \\frac{dy}{dx}       ->  y'(x)
    PDE 的 \\partial 形式不在此处处理，建议 PDE 直接用 Wolfram 语法（D[u[x,t], t] 等）。
    """
    def _deriv_n(m: re.Match) -> str:
        n = int(m.group(1))
        f = m.group(2)
        x = m.group(3)
        if n <= 3:
            return f"{f}{chr(39) * n}({x})"
        return f"{f}^{{({n})}}({x})"

    s = _LATEX_FRAC_DN.sub(_deriv_n, s)
    s = _LATEX_FRAC_D1.sub(r"\1'(\2)", s)
    return s


def _wrap_bare_functions(s: str, functions: list[str], variables: list[str]) -> str:
    """
    把 LaTeX 中"裸"的函数名包装成 f(x) 形式。
    例如 functions=['y'], variables=['x']: `y''(x) + y = 0` -> `y''(x) + y(x) = 0`
    跳过：紧邻 ( ' ^ _ 或字母的位置（已被使用 / 是更长标识符的一部分）。
    """
    if not functions or not variables:
        return s
    var_args = ",".join(variables)
    for f in functions:
        # (?<![\w\\])  前面不是字母数字 _ 或反斜杠（不在 \frac 等命令名内）
        # (?![\w(^_'])  后面不是字母数字 _ 也不是 ( ^ _ '
        pat = re.compile(rf"(?<![\w\\]){re.escape(f)}(?![\w(^_'])")
        s = pat.sub(f"{f}({var_args})", s)
    return s


def _latex_to_wolfram(session, tex: str, functions: list[str], variables: list[str]) -> str:
    """
    用 Mathematica 的 ToExpression[..., TeXForm] 把 LaTeX 转 Wolfram 表达式字符串（InputForm）。
    流程：
      1. 导数预处理：\\frac{d^n y}{dx^n} -> y''(x)
      2. 裸函数名包装：+ y  ->  + y(x)
      3. ToExpression[s, TeXForm, Hold] 解析
      4. Set -> Equal：LaTeX 单等号被默认解析为 Set，统一改 Equal
    """
    pre = _preprocess_latex_derivatives(tex)
    pre = _wrap_bare_functions(pre, functions, variables)
    escaped = pre.replace("\\", "\\\\").replace('"', '\\"')
    code = (
        f'ToString['
        f'  ToExpression["{escaped}", TeXForm, Hold]'
        f'  /. Set -> Equal'
        f'  // ReleaseHold,'
        f'  InputForm'
        f']'
    )
    out, _ = _evaluate_capture(session, code, PREVIEW_TIMEOUT)
    out = _decode_wl_string(out)
    if out.strip() in ("$Failed", "Null", ""):
        raise ValueError(f"无法把 LaTeX 解析为 Wolfram 表达式：{tex}")
    return out


_BARE_EQ = re.compile(r"(?<![=!<>:])=(?![=])")


def _detect_paren_function_calls(text: str, functions: list[str]) -> list[str]:
    """
    Wolfram 模式下用户最常犯的错：把函数应用写成 y(x) 而非 y[x]。
    Wolfram 把 y(x) 解释为乘法（y * x），不会报错，会得到错误结果。
    返回提醒文案列表（每个用到圆括号的函数名一条）；调用方决定是当 warning 还是 error。
    """
    msgs = []
    for f in functions or []:
        # f 不被字母/下划线包围（数字前缀允许，比如 2y(x) 也算误用），且后面紧跟可选空格+左括号
        pat = re.compile(rf"(?<![a-zA-Z_\\]){re.escape(f)}\s*\(")
        if pat.search(text):
            msgs.append(
                f"看起来 {f}(...) 用了圆括号 —— Wolfram 里函数应用得写方括号 {f}[...]，"
                f"圆括号会被当成乘法（{f}(x) ≡ {f}*x）。如果本意就是乘法可以忽略此提示。"
            )
    return msgs


def normalize_input(
    session, text: str, fmt: str,
    functions: list[str] | None = None,
    variables: list[str] | None = None,
    warnings_out: list[str] | None = None,
) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("输入为空")
    if len(text) > MAX_INPUT_LEN:
        raise ValueError(f"单条输入超过 {MAX_INPUT_LEN} 字符")
    actual = _resolve_fmt(text, fmt)
    if actual == "wolfram":
        _validate_safe_wolfram(text)
        # Wolfram 用 == 表示等式；裸的 = 是赋值，会被静默吞掉。
        # 这里直接拦下，给用户友好提示，而不是让 DSolve 收到 {0} 后给出错乱结果。
        if _BARE_EQ.search(text):
            raise ValueError(
                f"Wolfram 表达式里方程应当用 == 表示等式（你写的是 =，会被解释为赋值）：{text}"
            )
        # 圆括号误用：y(x) 应是 y[x]。这是软提示（不抛错），用户可能本意就是乘法。
        if warnings_out is not None:
            warnings_out.extend(_detect_paren_function_calls(text, functions or []))
        return text
    out = _latex_to_wolfram(session, text, functions or [], variables or [])
    _validate_safe_wolfram(out)
    return out


# ------------------------------------------------------------------
# 求解
# ------------------------------------------------------------------

def _join_list(items: list[str]) -> str:
    return ", ".join(items)


def _decode_wl_string(out) -> str:
    if isinstance(out, bytes):
        return out.decode("utf-8")
    return str(out)


def _wl_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _message_capture_code(expr_code: str, result_code: str = "res", timeout: int = SOLVE_TIMEOUT) -> str:
    return (
        "TimeConstrained["
        "Block[{$MessageList = {}, $Messages = {}}, "
        f"res = {expr_code}; "
        f"{{{result_code}, ToString[$MessageList, InputForm]}}"
        f"], {timeout}, {{$TimedOut, \"{{}}\"}}]"
    )


def _split_eval_pair(out) -> tuple[Any, str]:
    if isinstance(out, (list, tuple)) and len(out) >= 2:
        return out[0], _decode_wl_string(out[1])
    return out, ""


def _format_messages(msg_text: str) -> list[str]:
    text = (msg_text or "").strip()
    if not text or text in ("{}", "Null"):
        return []
    return [f"Wolfram 消息：{text[:500]}"]


def _evaluate_capture(session, expr_code: str, timeout: int = SOLVE_TIMEOUT, result_code: str = "res") -> tuple[Any, list[str]]:
    out = session.evaluate(wlexpr(_message_capture_code(expr_code, result_code, timeout)))
    result, msg_text = _split_eval_pair(out)
    if _decode_wl_string(result) == "$TimedOut":
        raise TimeoutError(f"Wolfram 求值超过 {timeout} 秒，已中止")
    return result, _format_messages(msg_text)


def _to_input_form(session, expr_code: str, timeout: int = SOLVE_TIMEOUT) -> tuple[str, list[str]]:
    out, messages = _evaluate_capture(session, expr_code, timeout, "ToString[res, InputForm]")
    return _decode_wl_string(out), messages


def _to_tex_form(session, expr_code: str, timeout: int = SOLVE_TIMEOUT) -> tuple[str, list[str]]:
    out, messages = _evaluate_capture(session, expr_code, timeout, "ToString[TeXForm[res]]")
    return _decode_wl_string(out), messages


def _plot_png_base64(session, plot_code: str) -> tuple[str, list[str]]:
    """plot_code 必须是合法 Wolfram 绘图表达式，例如 Plot[...] / Plot3D[...]."""
    code = (
        f"BaseEncode[ExportByteArray[{plot_code}, \"PNG\"], \"Base64\"]"
    )
    out, messages = _evaluate_capture(session, code, PLOT_TIMEOUT)
    # Mathematica 的 BaseEncode 默认每 76 字符插换行；Safari 的 atob() 不接受任何空白，
    # 所以这里把全部空白（\n \r \t 空格）一次性剥干净。
    return re.sub(r"\s+", "", _decode_wl_string(out)), messages


def _plot_legends_option(functions: list[str]) -> str:
    """多条曲线时按未知函数名生成 Mathematica PlotLegends 选项。"""
    if len(functions) <= 1:
        return ""
    labels = ", ".join(f'"{f}"' for f in functions)
    return f', PlotLegends -> Placed[{{{labels}}}, Right]'


def _build_dsolve(req: SolveRequest, eqs: list[str], conds: list[str]) -> tuple[str, str]:
    """返回 (DSolve 表达式字符串, 用于 TeXForm 的解表达式)."""
    full_eqs = eqs + conds
    eq_part = "{" + _join_list(full_eqs) + "}"
    fns = req.functions
    var = req.variables[0] if req.variables else "x"
    # DSolve 第二参数是函数 / 函数列表
    if len(fns) == 1:
        fn_part = f"{fns[0]}[{var}]"
    else:
        fn_part = "{" + ", ".join(f"{f}[{var}]" for f in fns) + "}"
    dsolve_expr = f"DSolve[{eq_part}, {fn_part}, {var}]"
    # 解的展示：First @ DSolve[...] 给出第一个解的 rule list
    sol_expr = f"First[{dsolve_expr}]"
    return dsolve_expr, sol_expr


def _build_pde_dsolve(req: SolveRequest, eqs: list[str], conds: list[str]) -> tuple[str, str]:
    full_eqs = eqs + conds
    eq_part = "{" + _join_list(full_eqs) + "}"
    fns = req.functions
    vars_ = req.variables
    if len(fns) == 1:
        var_tuple = "{" + _join_list(vars_) + "}"
        fn_part = f"{fns[0]}[{_join_list(vars_)}]"
    else:
        fn_part = "{" + ", ".join(f"{f}[{_join_list(vars_)}]" for f in fns) + "}"
        var_tuple = "{" + _join_list(vars_) + "}"
    dsolve_expr = f"DSolve[{eq_part}, {fn_part}, {var_tuple}]"
    sol_expr = f"First[{dsolve_expr}]"
    return dsolve_expr, sol_expr


def _build_ndsolve(req: SolveRequest, eqs: list[str], conds: list[str]) -> str:
    full_eqs = eqs + conds
    eq_part = "{" + _join_list(full_eqs) + "}"
    fns = req.functions
    if len(fns) == 1:
        fn_part = fns[0]
    else:
        fn_part = "{" + _join_list(fns) + "}"
    if not req.plot_range:
        raise ValueError("数值解需要提供自变量范围（如 {x, 0, 10}）")
    rng = req.plot_range.strip()
    return f"NDSolve[{eq_part}, {fn_part}, {rng}]"


def _ndsolve_plot_code(req: SolveRequest, sol_var: str) -> str:
    """根据未知函数 + 区间生成 Plot / Plot3D 调用代码。sol_var 是已绑定 NDSolve 结果的符号名。"""
    fns = req.functions
    vars_ = req.variables
    if not req.plot_range:
        raise ValueError("绘图需要 plot_range")
    rng = req.plot_range.strip()
    if len(vars_) == 1:
        var = vars_[0]
        if len(fns) == 1:
            traces = f"{fns[0]}[{var}] /. First[{sol_var}]"
        else:
            inner = ", ".join(f"{f}[{var}]" for f in fns)
            traces = f"{{{inner}}} /. First[{sol_var}]"
        return f"Plot[Evaluate[{traces}], {rng}{_plot_legends_option(fns)}]"
    elif len(vars_) == 2:
        # PDE：默认画第一个未知函数
        f = fns[0]
        v1, v2 = vars_
        # 取出 plot_range 第一个区间
        # plot_range 形如 "{x,0,Pi}, {t,0,1}"
        parts = [p.strip() for p in re.split(r"\}\s*,\s*\{", rng.strip("{} "))]
        if len(parts) >= 2:
            r1 = "{" + parts[0].strip("{}") + "}"
            r2 = "{" + parts[1].strip("{}") + "}"
            return f"Plot3D[Evaluate[{f}[{v1},{v2}] /. First[{sol_var}]], {r1}, {r2}]"
        return f"Plot3D[Evaluate[{f}[{v1},{v2}] /. First[{sol_var}]], {rng}]"
    else:
        raise ValueError("不支持的变量数")


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------

def solve(req: SolveRequest) -> SolveResult:
    warnings: list[str] = []
    try:
        _validate_request(req)
        session = get_session()
        with get_eval_lock():
            eqs = [
                normalize_input(session, e, req.fmt, req.functions, req.variables, warnings)
                for e in req.equations if e and e.strip()
            ]
            conds = [
                normalize_input(session, c, req.fmt, req.functions, req.variables, warnings)
                for c in req.conditions if c and c.strip()
            ]
            warnings = list(dict.fromkeys(warnings))
            if not eqs:
                return SolveResult(ok=False, error="至少需要一条方程")

            if req.mode == "analytic":
                if req.kind == "pde":
                    dsolve_expr, sol_expr = _build_pde_dsolve(req, eqs, conds)
                else:
                    dsolve_expr, sol_expr = _build_dsolve(req, eqs, conds)
                raw, msgs = _to_input_form(session, dsolve_expr)
                warnings.extend(msgs)
                if "$Failed" in raw:
                    return SolveResult(
                        ok=False,
                        error=f"Mathematica 未能求出解析解：{raw}",
                        normalized_eqs=eqs, normalized_conds=conds,
                        warnings=list(dict.fromkeys(warnings)),
                    )
                if raw.lstrip().startswith("DSolve["):
                    return SolveResult(
                        ok=False,
                        error=(
                            "Mathematica 无法求出该方程的解析解（DSolve 未化简）。"
                            "非线性方程或缺少边界/初值的复杂方程通常如此，"
                            "请改用「数值解（NDSolve）」模式，并提供绘图区间（如 {t, 0, 50}）。"
                        ),
                        raw=raw,
                        normalized_eqs=eqs, normalized_conds=conds,
                        warnings=list(dict.fromkeys(warnings)),
                    )
                tex, msgs = _to_tex_form(session, sol_expr)
                warnings.extend(msgs)
                img = ""
                if req.plot and req.plot_range:
                    sol_var = f"ClaudeSol${uuid.uuid4().hex}"
                    try:
                        _, msgs = _evaluate_capture(session, f"{sol_var} = {sol_expr}", SOLVE_TIMEOUT)
                        warnings.extend(msgs)
                        plot_code = _ndsolve_plot_code(req, sol_var)
                        if len(req.variables) == 1:
                            fns = req.functions
                            var = req.variables[0]
                            if len(fns) == 1:
                                traces = f"{fns[0]}[{var}] /. {sol_var}"
                            else:
                                inner = ", ".join(f"{f}[{var}]" for f in fns)
                                traces = f"{{{inner}}} /. {sol_var}"
                            plot_code = (
                                f"Plot[Evaluate[{traces}], {req.plot_range.strip()}"
                                f"{_plot_legends_option(fns)}]"
                            )
                        img, msgs = _plot_png_base64(session, plot_code)
                        warnings.extend(msgs)
                    finally:
                        session.evaluate(wlexpr(f"Clear[{sol_var}]"))
                return SolveResult(
                    ok=True, latex=tex, raw=raw, image_base64=img,
                    normalized_eqs=eqs, normalized_conds=conds,
                    warnings=list(dict.fromkeys(warnings)),
                )

            ndsolve_expr = _build_ndsolve(req, eqs, conds)
            sol_var = f"ClaudeSol${uuid.uuid4().hex}"
            try:
                _, msgs = _evaluate_capture(session, f"{sol_var} = {ndsolve_expr}", SOLVE_TIMEOUT)
                warnings.extend(msgs)
                raw, msgs = _to_input_form(session, sol_var)
                warnings.extend(msgs)
                if "$Failed" in raw or raw.lstrip().startswith("NDSolve["):
                    return SolveResult(
                        ok=False,
                        error=(
                            "Mathematica NDSolve 未能求解。常见原因：方程或条件不足以唯一确定解、"
                            "区间设置不合理、表达式有语法错误。原始返回："
                            + raw[:300]
                        ),
                        normalized_eqs=eqs, normalized_conds=conds,
                        warnings=list(dict.fromkeys(warnings)),
                    )
                img = ""
                if req.plot:
                    plot_code = _ndsolve_plot_code(req, sol_var)
                    img, msgs = _plot_png_base64(session, plot_code)
                    warnings.extend(msgs)
                return SolveResult(
                    ok=True, latex="", raw=raw, image_base64=img,
                    normalized_eqs=eqs, normalized_conds=conds,
                    warnings=list(dict.fromkeys(warnings)),
                )
            finally:
                session.evaluate(wlexpr(f"Clear[{sol_var}]"))

    except WolframLanguageException as e:
        return SolveResult(ok=False, error=f"Wolfram 报错：{e}")
    except ValueError as e:
        return SolveResult(ok=False, error=str(e))
    except TimeoutError as e:
        return SolveResult(ok=False, error=str(e))
    except Exception as e:
        return SolveResult(ok=False, error=f"求解失败：{e}")


def preview_expression(
    text: str, fmt: str,
    functions: list[str] | None = None,
    variables: list[str] | None = None,
) -> dict:
    """
    给前端"预览"用：把单条输入归一化成 Wolfram 表达式，并转成 TeXForm 用于渲染。
    返回 {ok, latex, wolfram, error}。不解微分方程，只做一次表达式解析。
    """
    session = get_session()
    warnings: list[str] = []
    try:
        _validate_ident_list("未知函数", functions or [])
        _validate_ident_list("自变量", variables or [])
        with get_eval_lock():
            wolfram_expr = normalize_input(session, text, fmt, functions, variables, warnings)
            tex, msgs = _to_tex_form(session, wolfram_expr, PREVIEW_TIMEOUT)
            warnings.extend(msgs)
        if "$Failed" in tex or not tex.strip():
            return {"ok": False, "error": f"无法渲染表达式：{tex}", "warnings": warnings}
        return {
            "ok": True, "latex": tex, "wolfram": wolfram_expr,
            "warnings": list(dict.fromkeys(warnings)), "error": "",
        }
    except WolframLanguageException as e:
        return {"ok": False, "error": f"Wolfram 报错：{e}", "warnings": warnings}
    except ValueError as e:
        return {"ok": False, "error": str(e), "warnings": warnings}
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": f"预览失败：{e}", "warnings": warnings}


if __name__ == "__main__":
    # 命令行自检
    cases = [
        # 用例 1：解析解 ODE
        SolveRequest(
            kind="ode", mode="analytic",
            equations=["y''[x] + y[x] == 0"],
            functions=["y"], variables=["x"],
        ),
        # 用例 2：初值问题
        SolveRequest(
            kind="ode", mode="analytic",
            equations=["y''[x] + y[x] == 0"],
            conditions=["y[0] == 1", "y'[0] == 0"],
            functions=["y"], variables=["x"],
        ),
        # 用例 3：ODE 组数值解 + 画图
        SolveRequest(
            kind="ode_system", mode="numeric",
            equations=["x'[t] == y[t]", "y'[t] == -x[t]"],
            conditions=["x[0] == 1", "y[0] == 0"],
            functions=["x", "y"], variables=["t"],
            plot_range="{t, 0, 2 Pi}", plot=True,
        ),
        # 用例 6：LaTeX 简单写法
        SolveRequest(
            fmt="latex", kind="ode", mode="analytic",
            equations=["y''(x) + y(x) = 0"],
            functions=["y"], variables=["x"],
        ),
        # 用例 7：LaTeX 复杂写法
        SolveRequest(
            fmt="latex", kind="ode", mode="analytic",
            equations=[r"\frac{d^2 y}{dx^2} + y = 0"],
            functions=["y"], variables=["x"],
        ),
    ]
    for i, c in enumerate(cases, 1):
        print(f"=== case {i} ===")
        r = solve(c)
        print("ok:", r.ok)
        print("normalized:", r.normalized_eqs, r.normalized_conds)
        print("latex:", r.latex[:200])
        print("raw:", r.raw[:200])
        if r.image_base64:
            print("image bytes:", len(r.image_base64))
        if r.error:
            print("error:", r.error)
