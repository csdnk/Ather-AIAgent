# P3 平台交互 Mock · PRD V1.3

更新：2026-09-22。打开 [统一演示入口](index.html)，无需连接后端。参考用户提供的两个平台原型重新设计了工作台、侧栏、表格、表单和详情抽屉，支持深浅主题及窄屏。

提供业务使用、运行观察、评审验证三个观察视角，共 13 个页面。23 个专题、161 个步骤覆盖 FR01—FR18 及公共底座、Remember/Embedding、Recall、Operate 的关键流程和异常分支。

- [使用与演示说明](原型Mock设计与演示说明.md)：页面用途、演示顺序与边界。
- [需求与流程覆盖](Mock需求与流程覆盖_20260922.md)：逐项映射、歧义处理及待确认细则。
- [本次验收](../测试与验收/Mock前端重设计与流程覆盖验收_20260922.md)：测试结果与限制。
- [PRD V1.3](../PRD版本/AetherBrain_P3_PRD_租户补全版_V1.3.docx) / [完整流程图](../架构设计/P3全景流程_Excalidraw/P3_整体架构与完整流程.excalidraw)。

运行时请保留整个目录。当前依赖为 review-model.js、review-content.js、platform-model.js、platform-content.js、platform-app.js 和 platform.css；前两个文件是仍在使用的基础模型和场景。tests 目录保留原有及新增的模拟回归测试，可用 Node.js 运行：

```text
node --test tests/review-model.test.cjs tests/platform.test.cjs
```

直接打开 HTML 可运行；如使用静态服务，请以 aether 项目根目录为站点根目录，以便访问真实代码接入说明。会话状态保存在当前标签页，浏览器禁止会话存储时仍可在页面内存中演示。分享时复制整个目录；连同文档分享请保留交付成果及必要相对目录结构。

旧 tenant.html、p3-extended.html 保留跳转；[P2 消费场景](p2.html)是独立历史参考。所有模型、身份、检索、搬运和时钟均为模拟，不代表后端已实现或通过真实验收。
