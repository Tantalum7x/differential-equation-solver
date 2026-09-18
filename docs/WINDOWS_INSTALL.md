# Windows 新手安装教程

这份教程面向第一次在 Windows 上运行本项目的用户。目标是让你在自己的电脑上打开网页：

```text
http://127.0.0.1:5001
```

并能正常调用 Mathematica/Wolfram Engine 求解方程。

## 1. 需要准备什么

你需要安装：

- Windows 10 或 Windows 11
- Python 3.10 或更新版本
- Mathematica 或 Wolfram Engine
- 本项目代码

如果只是访问别人共享出来的网页，你不需要安装 Python 和 Mathematica，只需要浏览器。

## 2. 安装 Python

1. 打开 Python 官网下载页：<https://www.python.org/downloads/windows/>
2. 下载 Windows installer。
3. 运行安装程序。
4. 勾选 `Add python.exe to PATH`。
5. 点击 `Install Now`。

安装完成后，打开 PowerShell，输入：

```powershell
py --version
```

能看到 Python 版本号即可，例如：

```text
Python 3.13.7
```

如果提示找不到 `py`，通常是安装时没有加入 PATH。可以重新运行安装程序，选择 Modify，并勾选 PATH 相关选项。

## 3. 安装 Mathematica 或 Wolfram Engine

安装 Mathematica 后，程序会自动尝试查找 `WolframKernel.exe`。常见路径类似：

```text
C:\Program Files\Wolfram Research\Mathematica\14.0\WolframKernel.exe
```

如果使用 Wolfram Engine，常见路径类似：

```text
C:\Program Files\Wolfram Research\Wolfram Engine\14.0\WolframKernel.exe
```

版本号可能不同，例如 `13.3`、`14.0`、`14.1`，以你电脑上的实际目录为准。

## 4. 准备项目目录

建议把项目放在一个简单路径下，例如：

```text
C:\diff-eq-solver
```

后续教程都假设项目目录是 `C:\diff-eq-solver`。如果你的目录不同，把命令里的路径替换成自己的项目路径即可。

打开 PowerShell，进入项目目录：

```powershell
cd C:\diff-eq-solver
```

确认目录里有这些文件：

```powershell
dir
```

至少应该能看到：

```text
app.py
solver.py
requirements.txt
templates
static
```

## 5. 创建 Python 虚拟环境

在项目目录下运行：

```powershell
py -3 -m venv .venv
```

这会创建一个 `.venv` 文件夹，用来隔离本项目的 Python 依赖。

## 6. 安装项目依赖

继续在项目目录下运行：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果下载很慢，可以换网络后重试。依赖安装成功后，不需要全局安装 Flask 或 wolframclient。

## 7. 设置 WolframKernel 路径

大多数情况下程序可以自动找到 Mathematica。如果启动时报找不到 kernel，再手动设置 `WOLFRAM_KERNEL`。

PowerShell 当前窗口临时设置：

```powershell
$env:WOLFRAM_KERNEL="C:\Program Files\Wolfram Research\Mathematica\14.0\WolframKernel.exe"
```

如果你的实际路径不同，请替换成自己的 `WolframKernel.exe` 路径。

可以用这个命令检查文件是否存在：

```powershell
Test-Path $env:WOLFRAM_KERNEL
```

返回 `True` 表示路径正确。

## 8. 启动程序

在项目目录下运行：

```powershell
.\.venv\Scripts\python.exe app.py
```

看到类似输出表示启动成功：

```text
Wolfram kernel warmed up
Running on http://127.0.0.1:5001
```

打开浏览器访问：

```text
http://127.0.0.1:5001
```

需要停止程序时，在运行服务的 PowerShell 窗口按 `Ctrl+C`。

## 9. 第一次测试

在页面中点击“示例（点击展开）”，选择第一个示例“Wolfram ODE”，点击“应用此示例”，然后点击“求解”。

如果正常，会看到类似下面的解析解：

```text
y(x) = c1 cos(x) + c2 sin(x)
```

## 10. 局域网共享给同学

如果这台 Windows 电脑安装了 Mathematica，其他同学没安装，也可以让他们通过浏览器访问你的电脑。

在 PowerShell 中运行：

```powershell
$env:ACCESS_TOKEN="demo123"
$env:HOST="0.0.0.0"
$env:PORT="5001"
.\.venv\Scripts\python.exe app.py
```

查看自己的局域网 IP：

```powershell
ipconfig
```

找到当前网络适配器下的 `IPv4 地址`，例如：

```text
192.168.1.23
```

同一局域网的朋友访问：

```text
http://192.168.1.23:5001
```

页面会要求输入访问口令，填：

```text
demo123
```

如果朋友打不开，检查 Windows 防火墙是否允许 Python 通过网络通信。

## 11. 公网临时演示

公网演示推荐使用 Cloudflare Quick Tunnel。Windows 上可以下载 `cloudflared.exe`，也可以用包管理器安装。

如果你已经有 `winget`，可以尝试：

```powershell
winget install Cloudflare.cloudflared
```

启动本项目，保持只监听本机：

```powershell
$env:ACCESS_TOKEN="demo123"
$env:HOST="127.0.0.1"
$env:PORT="5001"
.\.venv\Scripts\python.exe app.py
```

另开一个 PowerShell，运行：

```powershell
cloudflared tunnel --url http://127.0.0.1:5001
```

命令会输出一个公网 HTTPS 地址，通常类似：

```text
https://xxxx-xxxx.trycloudflare.com
```

把这个地址和访问口令发给朋友即可。

## 12. 常见问题

### `py` 不是内部或外部命令

Python 没有正确安装，或没有加入 PATH。重新安装 Python，并勾选 `Add python.exe to PATH`。

### 找不到 `WolframKernel`

设置 `WOLFRAM_KERNEL`，并用 `Test-Path $env:WOLFRAM_KERNEL` 检查路径是否正确。

### pip 安装失败

先升级 pip：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

如果仍失败，通常是网络问题，换网络后重试。

### 端口 5001 被占用

换一个端口：

```powershell
$env:PORT="5050"
.\.venv\Scripts\python.exe app.py
```

然后访问：

```text
http://127.0.0.1:5050
```

### 局域网朋友打不开页面

检查三件事：

- 程序是否用 `HOST=0.0.0.0` 启动。
- 朋友访问的是你的局域网 IPv4 地址，不是 `127.0.0.1`。
- Windows 防火墙是否允许 Python 通过。

### 页面提示访问口令错误

确认页面中输入的口令和 PowerShell 里设置的 `$env:ACCESS_TOKEN` 完全一致。
