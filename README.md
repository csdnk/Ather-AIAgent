# aether-agent-memory

`aether-agent-memory` 是一个基于 `src/` 布局的 Python 项目，当前仓库包含：

- 核心包：`src/aether_agent_memory`
- 单元测试：`tests/unit`
- 交互式演示：`examples/minimal_loop`

这份 README 面向第一次下载仓库的新用户，说明需要准备什么环境、安装哪些依赖，以及怎样把项目跑起来。

## 1. 环境要求

- Python `3.13`
  说明：仓库根目录的 `.python-version` 和 `pyproject.toml` 都要求 `3.13`
- 推荐使用 `uv` 管理虚拟环境和依赖
- 操作系统不限，下面命令优先按跨平台写法给出

如果你已经安装好 Python，可以先确认版本：

```bash
python --version
```

如果输出不是 `3.13.x`，建议先切换或安装 Python 3.13。

## 2. 克隆仓库

```bash
git clone <your-repo-url>
cd aether-agent-memory
```

## 3. 推荐安装方式：使用 uv

如果本机还没有 `uv`，请先按 `uv` 官方文档完成安装，并确认下面命令可用：

```bash
uv --version
```

### 3.1 只安装基础依赖

如果你只想安装项目主依赖：

```bash
uv sync
```

### 3.2 安装测试依赖

如果你需要运行测试、类型检查、lint：

```bash
uv sync --group dev
```

### 3.3 安装演示依赖

如果你要运行 Textual TUI 演示：

```bash
uv sync --group demo
```

### 3.4 一次性装齐开发 + 演示依赖

如果你希望本地开发、测试和演示都能直接运行，推荐一次性同步：

```bash
uv sync --group dev --group demo
```

## 4. 备用安装方式：使用 venv + pip

如果你暂时不使用 `uv`，也可以这样安装：

### 4.1 创建虚拟环境

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 4.2 安装基础依赖

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

### 4.3 如果要跑测试，再安装这些工具

```bash
python -m pip install pytest pytest-asyncio pytest-cov mypy ruff pre-commit
```

### 4.4 如果要跑 TUI 演示，再安装 Textual

```bash
python -m pip install textual
```

## 5. 如何运行

### 5.1 运行单元测试

如果你使用 `uv`：

```bash
uv run pytest
```

只跑单元测试：

```bash
uv run pytest tests/unit -m unit
```

### 5.2 运行 lint 和类型检查

```bash
uv run ruff check src tests
uv run mypy src
```

### 5.3 运行 P3 数据流框架 TUI 演示

```bash
uv run python examples/minimal_loop/main.py
```

如果你希望演示一步一步自动推进更快一点，可以传入更短的延时：

```bash
uv run python examples/minimal_loop/main.py --step-delay 0
```

说明：

- 演示入口已经在代码里自动补了 `src` 路径
- 但运行前仍然需要先安装 `textual`

## 6. Makefile 中可直接使用的命令

如果你在本地有 `make` 环境，也可以直接使用仓库里的快捷命令：

```bash
make install
make lint
make typecheck
make test
```

注意：

- 当前 `Makefile` 更偏向类 Unix 环境
- 在 Windows 上更推荐直接使用前面的 `uv run ...` 命令

## 7. 新用户最常见的两条路径

### 只想把 demo 跑起来

```bash
uv sync --group demo
uv run python examples/minimal_loop/main.py --step-delay 0
```

### 想完整参与开发

```bash
uv sync --group dev --group demo
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run python examples/minimal_loop/main.py --step-delay 0
```

## 8. 常见问题

### 8.1 `uv` 命令不存在

说明本机还没有安装 `uv`，请先安装它，或改用上面的 `venv + pip` 方案。

### 8.2 `ModuleNotFoundError: No module named 'aether_agent_memory'`

通常是因为：

- 没有先安装项目依赖
- 没有进入虚拟环境

建议优先执行：

```bash
uv sync --group demo
uv run python examples/minimal_loop/main.py
```

### 8.3 `textual` 缺失，TUI 无法启动

请补装演示依赖：

```bash
uv sync --group demo
```

或者使用 `pip`：

```bash
python -m pip install textual
```
