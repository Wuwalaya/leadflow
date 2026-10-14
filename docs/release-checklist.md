# 发布前检查单

详细基线、验证结果与剩余风险见[验证记录](verification.md)。本轮只准备公开候选文件，不 commit/push。

## 已完成的工程检查

- [x] 确认公开仓库 origin 为 Wuwalaya/leadflow，源与目标起始状态干净，记录源稳定 SHA。
- [x] 选择性同步源码与 127 项离线测试，保留公开版合理接口和历史。
- [x] 不复制 `.git`、私人配置、运行库、真实响应或私人交接资料。
- [x] 默认 reference 使用明确标记的合成 Fixture；评估入口不依赖隐藏历史目录。
- [x] 中英文 README 命令与能力边界一致；中文 docs / CHANGELOG / AGENTS 已审查。
- [x] 完整测试、Brazil / USA 配置、临时库两次 reference、评估命令、CSV、相对链接通过。
- [x] 从临时 Git 索引导出的干净公开候选快照通过离线验证。
- [x] 审查待发布实际文本和公开可达历史；运行产物、凭据与私人资料保持忽略。
- [x] 不读取真实 `.env`、不调用付费 API、不使用真实客户数据库、不修改源仓库。

## 等待维护者人工审阅

- [ ] 审阅完整差异与新增文件，确认同步范围及合成数据标识。
- [ ] 接受历史 API 说明的证据限制，核对美国小规模验证的描述。
- [ ] 确认继续保留已公开的少量第三方官网摘录；本轮未重核授权及时效。
- [ ] 审阅历史扫描局限；如另发现历史敏感内容，单独决定处置，不直接改写历史。
- [ ] 明确选择文件暂存并复查 staged diff，再由维护者决定 commit/push。
- [ ] 发布后从 GitHub 新克隆，重跑 README 离线命令；此前不能称远程版本已同步。

建议提交信息：`Prepare reproducible public release with Chinese-first documentation`

建议仓库 Description：`独立开发的 B2B 海外客户发现与筛选原型：官网证据、Jev 语义筛选、SQLite 去重与人工审核。`

建议 Topics：`python`、`b2b`、`lead-discovery`、`tavily`、`sqlite`、`cli`、`evidence-based`、`human-in-the-loop`。
