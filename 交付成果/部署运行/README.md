# 部署与运行

2026-10-06：旧系统已恢复至独立域名，新若依版本保留原地址。[双版本访问与恢复说明](旧系统恢复与双版本访问说明_20261006.md)。

新若依：[管理后台](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/ruoyi/) / [Agent](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/ruoyi-agent/) / [账号与使用说明](若依云端使用说明_20261006.md)。

旧系统入口：[Agent](https://aether-legacy-c50c3827.southeastasia.cloudapp.azure.com/agent/) / [管理后台](https://aether-legacy-c50c3827.southeastasia.cloudapp.azure.com/app/default%20workspace/aether-admin) / [Builder](https://aether-legacy-c50c3827.southeastasia.cloudapp.azure.com/builder)。[完整页面与账号清单](项目Web页面与测试账号_20261005.md)；[Azure 发布记录](Agent平台Azure发布记录_20261005.md)。旧日期报告只作为历史证据。

- [云端 P3 API 接口文档](https://aether-legacy-c50c3827.southeastasia.cloudapp.azure.com/docs)：平台管理员登录后查看；[部署与使用说明](P3_API接口文档云端使用说明_20261005.md)。

- [打开云端 Temporal](打开Temporal运行页面.ps1)：先登录管理后台的 `platform_admin` 账号，不再依赖 8233 本机端口。

- [P3 本机 Docker 运行指南（2026-10-04）](P3_本机Docker运行指南_20261004.md)：本机正式 Temporal 与真实 Azure 业务依赖；[启停/状态/验证脚本](P3_本机Docker.ps1)、[运行验收](../测试与验收/P3_本机Docker运行验收_20261004.md)、[管理员权限清单](P3_Azure权限与配置汇总_20261004.md)。

- [P3 多环境配置与 AKS 接入设计（2026-10-02，V1.1）](../架构设计/P3_多环境配置与AKS接入设计_20261002.md)：三个环境统一 PG/Ceph/Redis/Milvus，复用已有 `Ceph-Cluster`，取消 SQLite 运行模式；当前为待评审设计。
- [P4 接入与真实业务闭环计划](../开发协作/P4接入当前P3与业务闭环验证_20261001.md)：本机 P4 接 Azure P3，然后部署 AKS；当前为待实施计划。
- [多场景跨会话云端实测](../测试与验收/P3_多场景跨会话验证方案与实测_20261001.md)：独立HTTP客户端18项执行15通过、3未通过；保留无答案和混合主题相关性失败，P4 Web尚未执行。
- [AKS 实际部署进度与验证（2026-10-01）](P3_AKS部署进度_20261001.md)：P3/Web/Temporal 已运行；基础链路和持久化验证通过，立即召回与 Temporal 重启后自动恢复的限制已记录。
- [现有 AKS 联调与演示部署指南（2026-10-01）](P3_AKS联调演示部署指南_20261001.md)：Southeast Asia、ACR 构建、单实例/PVC、Temporal、初始化、访问与重启检查；已执行情况见部署进度。
- [Temporal 本地运行与迁移指南（2026-09-30）](P3_Temporal本地运行与迁移指南_20260930.md)：统一服务入口、持久任务、旧目录切换与运行边界。

- [Working 向量召回与历史补索引（2026-09-30）](Working向量召回与历史补索引_20260930.md)：saved/ready、会话范围、任务恢复与显式补索引。

- [P3 统一服务运行指南（2026-09-26）](P3_统一服务运行指南_20260926.md)：安装、初始化、模型/P2 配置、HTTP 接口、持续运行及容器部署。
- [统一运行整合与工程验收](../测试与验收/P3_统一运行整合与验收_20260926.md)：PRD/流程接线、已验证结果和待真实环境验收项。
