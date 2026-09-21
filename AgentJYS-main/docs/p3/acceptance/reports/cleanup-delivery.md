# 旧代码清理验收

日期：2026-09-19。本次只删除已确认闲置代码，不改变三个流程的功能范围。

- 删除 4 个 Python 文件，共 202 行；同时删除旧面板脚本的 Ruff 豁免。
- 清理前对 687 个文本文件做候选引用复核，并检查源码逻辑、导入、懒加载、公开符号、测试、安装入口、Docker/Compose、CI 和文档。
- 删除前完成三流程/底座/契约基线及 14 项架构兼容测试；备份按原路径保存到仓库外，SHA256 已核对。
- 未修改或移除任何测试；模型缓存、业务数据库、日志及其他已有改动保留。

逐项原因与保留范围见[清理说明](../../development/08_旧代码清理与保留边界.md)，机器可读证据见[清理清单](cleanup-manifest.json)。

## 清理后验证

| 检查 | 结果与证据 |
|---|---|
| 当前三流程、日志与健康检测 | 34 项通过；[回归报告](cleanup-regression.json)全部检查通过 |
| 公共底座与契约、Schema、Ruff、Mypy | 底座 33 项、契约 189 项通过；72 个 Schema 一致性检查、Ruff 与 Mypy 通过 |
| 旧 Recall 全目录 | 126 项通过，包含 14 项架构/兼容检查；测试配置启用 asyncio auto |
| 原生 Embedding 适配测试 | 8 项通过 |
| 真实 BGE CPU 三流程 | 通过；512 维 Query/Passage、写入、召回、纠错、删除、自动缓存动作、重开绑定、Trace、健康检测；[真实验证报告](cleanup-native-validation.json) |
| 安装入口与删除范围 | 见清理清单中的结构检查；入口文件保留不等于已完成容器部署验证 |

合计 390 项测试通过（34 + 33 + 189 + 126 + 8，不重复计入删除前基线），另完成真实模型与本地三流程演示。当前三流程 CLI 的帮助入口、源码语法、文档链接和 `git diff --check` 均通过。

真实模型报告记录 10 次原生调用及对应证据；本次使用已有缓存模型，没有以测试向量替代真实推理。自动动作仍属于当前本地文件缓存实现，不代表 PRD 全部分层调度能力。

额外旧 Recall 回归初次因独立验证环境缺少 `pytest-asyncio` 未执行异步用例；在仓库外环境补齐插件、启用项目的 auto 模式后，126 项全部通过。文档生成期间的链接及 Windows 输出编码问题在最终校验中统一检查。原始失败与重跑输出留在仓库外工作目录，最终报告只代表完成后的状态。

## 保留与限制

旧 API/Worker/Sidecar 仍被配置入口使用；兼容转发模块仍有测试契约；共享 `dashboard_page.py` 仍被 HTTP 路由使用，所以没有删除。清理不等同于宣布旧系统整体退役。

本次未运行旧容器全栈、真实 Milvus/LangMem、Azure/AKS、生产存储分层及长稳产品验收，也不覆盖未知仓库外消费者。

## 复核命令

仓库根目录、现有独立 Python 3.13 验证环境中执行；Windows 可先设置 `PYTHONUTF8=1`，保证中文报告在子进程间使用一致编码。

```text
python scripts/p3/validate_flows.py --report docs/p3/acceptance/reports/cleanup-regression.json
python scripts/p3/validate_native_flows.py --report docs/p3/acceptance/reports/cleanup-native-validation.json
python -m pytest tests/unit/recall -q
git diff --check
```

旧 Recall 测试使用仓库 `pyproject.toml` 的 `asyncio_mode=auto`，需安装已有开发依赖 `pytest-asyncio` 并使 `src` 在 Python 导入路径内。该验证环境位于仓库外；真实模型测试还需要 `embedding-onnx` 依赖与已下载的配置模型。
