# 后端自动测试使用说明

本项目提供 `backend_tests.py`，用于直接测试 `solver.py` 的后端能力，不需要启动 Flask 页面。

## 前提

- 已安装 Python 依赖：`flask`、`wolframclient`
- 已安装 Mathematica 或 Wolfram Engine
- 程序能够自动找到 `WolframKernel`，或已设置 `WOLFRAM_KERNEL`

macOS / Linux:

```bash
.venv/bin/python backend_tests.py --list
.venv/bin/python backend_tests.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe backend_tests.py --list
.\.venv\Scripts\python.exe backend_tests.py
```

## 常用命令

列出默认会运行的用例：

```bash
.venv/bin/python backend_tests.py --list
```

运行默认测试集：

```bash
.venv/bin/python backend_tests.py
```

默认测试集会跳过较慢的 PDE 3D 绘图用例。要包含慢速用例：

```bash
.venv/bin/python backend_tests.py --include-slow
```

只运行某一组：

```bash
.venv/bin/python backend_tests.py --group analytic
.venv/bin/python backend_tests.py --group numeric
.venv/bin/python backend_tests.py --group security
```

可以重复指定多个组：

```bash
.venv/bin/python backend_tests.py --group numeric --group messages
```

只运行某个具体用例：

```bash
.venv/bin/python backend_tests.py --case numeric-system-plot
```

失败时立即停止：

```bash
.venv/bin/python backend_tests.py --failfast
```

## 用例分组

- `preview`：表达式预览、LaTeX 解析
- `analytic`：`DSolve` 解析解
- `numeric`：`NDSolve` 数值解和绘图
- `pde`：PDE 数值解，默认跳过，需 `--include-slow`
- `validation`：输入校验和用户常见错误
- `security`：危险 Wolfram 调用拦截
- `messages`：Wolfram 消息捕获与回传

## 结果解释

每个用例会打印运行耗时。失败时会显示 `SolveResult` 的主要字段，包括：

- `ok`
- `error`
- `raw`
- `latex`
- `normalized_eqs`
- `normalized_conds`
- `warnings`

如果出现 `未找到 WolframKernel`，请设置 `WOLFRAM_KERNEL`。

Windows 示例：

```bat
set WOLFRAM_KERNEL=C:\Program Files\Wolfram Research\Mathematica\14.0\WolframKernel.exe
```

## 注意

这些测试会启动本机 Wolfram kernel。第一次运行较慢是正常现象。PDE 和绘图用例耗时更长，演示前建议先跑一次默认测试集确认环境正常。
