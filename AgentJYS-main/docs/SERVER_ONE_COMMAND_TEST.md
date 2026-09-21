# 服务器一键测试

本项目的 Markdown、脚本和数据集清单均使用 UTF-8 编码。服务器运行脚本时会显式设置 `LANG=C.UTF-8`，避免终端环境把 UTF-8 中文错误显示为乱码。

## 前置条件

在项目根目录已构建以下两个镜像，并且保留师兄原项目中的 B1 模型缓存：

```bash
docker image inspect aether-p3:acceptance
docker image inspect aether-p3-engine:acceptance
ls /home/root-nuist/wps/agent/.aether/b1/models/fast-bge-small-zh-v1.5
```

## 执行

```bash
cd /home/root-nuist/wps/agent-bug
bash scripts/run_server_acceptance.sh
```

脚本会在独立 Docker 网络中启动 Redis、etcd、MinIO、Milvus、B1、P2、B2/Celery 和 P3，依次运行：Python 回归、B1→P2→B2→B3 闭环、B2 Working Memory、B2 压缩审计、B3 基线以及含 BEAM 的 B2 Acceptance 回放。生产镜像未包含 pytest 时，脚本只会在一次性测试容器中临时安装它，不会修改服务器 Python 环境或已有镜像。

结果写入 `artifacts/server_<时间戳>/`。默认结束后仅删除名称以 `agent-bug-` 开头的本次测试容器；不会删除镜像、数据卷、Docker 网络或服务器中其他项目的容器。

若要保留容器以便查看日志：

```bash
bash scripts/run_server_acceptance.sh --keep-containers
```

若还需要运行 B1 吞吐测试（会持续占用 CPU 数分钟）：

```bash
bash scripts/run_server_acceptance.sh --with-b1-throughput
```

## 中文显示排查

如果 MobaXterm 中中文显示异常，先在当前终端执行：

```bash
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
```

这只影响当前终端，不会修改服务器系统语言设置或项目文件编码。
