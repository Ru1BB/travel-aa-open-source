# AI agent 接手指南

## 工作原则

- 本文件与 Windows 上的 AGENTS.md 是同一文件，勿创建大小写重复副本。
- 先读 README.md、REQUIREMENTS.md、IMPORT.md，再检查 Git 状态与相关源码。
- 保留现有账号、账本、上传文件和历史快照，不将清库作为更新步骤。
- 发布、推送和业务备份均为手动操作，需要任务明确授权；不设置自动发布或自动备份。
- 不读取或传播私钥正文，不提交真实支付流水、数据库、账户凭据或本机私有路径。
- 运行界面/API 测试时使用独立端口和临时数据库，不操作正式数据目录。

## 当前功能及关键规则

- 仅网页版；管理员创建账号和独立旅程。普通用户只能进入分配给自己的旅程。
- 微信/支付宝个人 CSV、XLSX 导入，日期包含开始和结束两端整日，导入后全部待确认。
- 账单 owner 为上传者；只有 owner 能编辑、删除、确认，即使管理员也不能修改他人账单。
- 上方表单仅手动新增；下方每笔提供编辑、上传、删除按钮和多选。
- 上传表示确认并应用编辑，不是重复上传原文件。pending/private 不参与结算。
- billDrafts Map 暂存编辑；批量编辑仅修改共同/私人与分摊成员。重新打开应恢复已保存草稿，一致设置预填，混合设置保留各笔原值。
- POST /api/trips/{id}/batch 最多 200 笔，验证所有权、成员、旅程及版本；任意错误整批回滚。
- PATCH /api/trips/{id}/members 管理成员，members_before 防止并发覆盖，冲突返回 409。
- trip_exclusions 标记退出，不删除 trip_members、旧账单或快照。退出者失去访问和新增分摊资格；旧共同消费保留原身份、分摊和垫付。重新加入恢复原顺序，新成员追加。
- 以整数分均分，尾差按成员顺序，净额=垫付-应承担。转账建议能平账，不保证最少笔数，不真实付款。
- Excel 输出个人汇总、共同明细、转账三表；结算 records 快照不随之后改账变化。
- 文件删除仅删除物理原件/导出文件，不改已解析账单或快照。
- scrypt 盐哈希、12 小时会话、HttpOnly/SameSite Cookie、请求来源校验、登录限制；密码 6–128 字符。
- 初始管理员由私有环境变量提供，仅空数据库创建，不重置现有账号；未配置时可本机初始化，公网 setup 禁止。

## 架构与入口

| 文件 | 职责 |
| --- | --- |
| web_server.py | 标准库 HTTP 服务、SQLite、API、认证/权限、文件和快照 |
| billing.py | 金额、导入筛选、去重标识、结算、OOXML 输出 |
| app.js / index.html / style.css | 原生网页与状态、草稿、成员后台 |
| settlement.js | JavaScript 结算纯函数，生产以后端为准 |
| test_backend.py / settlement.test.js | 合成数据、权限、金额守恒与并发测试 |
| samples/ | 虚构支付账单，不能换成真实流水 |
| deploy/ | 白名单打包、参数化模板、手动发布及回退 |
| legacy/ | 旧本地浏览器应用，非生产入口，不包含在部署包或公开快照 |

数据库表：users、sessions、trips、trip_members、trip_exclusions、bills、files、records。以代码中的确切列定义为准；结构变更须评估历史数据与回退安全。

## 解析边界

前 100 行查找表头；CSV 为 UTF-8-sig/GB18030；XLSX 第一张表、共享/内联字符串、1900 数值日期。支持两平台别名、前置说明行、制表符与防数字转换反引号。仅有效正金额消费支出成为候选，跳过收入、退款、失败、转账红包和资金往来；部分退款人工核实。

上限 5 MB、10,000 行；XLSX 最多 200 压缩项、20 MB 解压、100 列。拒绝加密、公式、实体、路径穿越和 1904 日期系统。同旅程/owner/平台交易标识去重，无单号采用组合指纹并可能误判同秒同额交易。详细规则见 IMPORT.md。

## 开发与验证

```sh
python web_server.py
python -m unittest test_backend
node --test
node --check app.js
python -m unittest discover -s deploy -p 'test_*.py'
```

应用不需要第三方 Python/npm 包。server.mjs 只是启动包装，build.mjs 只复制静态资源，不能部署 dist 代替后端。测试不得使用真人账单；验证结果以当次实际运行日志为准。

## 部署约定

源码 /srv/travel-aa/releases/<release>，链接 /srv/travel-aa/web-current；持久数据 /var/lib/travel-aa；systemd 服务 travel-aa，低权限运行；后台默认 127.0.0.1:5182，Nginx 提供 HTTPS。

上述为项目目录约定，不包含任何个人部署地址。deploy/update.ps1 要求显式 ServerHost/PublicHost，可指定 SshUser、CertificateName、BackendPort 和 KeyPath。deploy/known_hosts 是本机核实的私有配置，不能禁用主机验证。模板必须经 render_config.py 渲染，不能把 __PUBLIC_HOST__ 等占位符直接安装。凭据与证书私钥仅留服务器；发布不复制 data/ 或清库。

## 开源准备状态

公开文档已移除个人运维历史；部署地址已参数化；真实样本只用于本机验证，不在仓库。旧 Git 历史可能仍含本地路径和部署信息，不能以最新文件扫描代替历史检查。公开快照须仅包含审核过的源码和合成样例，不携带旧 .git、业务文件或 legacy/。

许可证为 MIT（见 LICENSE），公开仓库使用全新历史，原私有仓库保留。手动同步公开仓库时只从审核源码更新，不合并旧私有历史。不要未经明确授权重写 GitHub 历史、强推或删除远程分支。
