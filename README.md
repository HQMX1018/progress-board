# CardOrder 协作看板

独立内网服务：`http://192.168.31.145:9090`。Python 3.12+ 标准库，无第三方依赖。

## 数据与身份

- Bug 平台数据库通过 SQLite `mode=ro` + `query_only` 读取，不修改原平台表或服务。
- 看板自己的计划、工作子项、操作历史、反馈、会话及 Agent 密钥存入独立 `data/board.db`。
- 登录标识沿用 Bug 平台已有 userid，沿用现有内网平台的无密码身份识别方式。
- 浏览器使用独立 HttpOnly 会话；Agent 密钥绑定创建人，只能更新本人工作，支持撤销。
- 历史 Bug 状态按流转记录还原；需求来源缺少完整历史与独立完成时间，页面注明限制。
- 已合并记完成，非 Bug / 重复仅归档；测试通过仍待合并。进度由真实流转映射三阶段，不伪造拆分子任务。
- 成员关联不等于独立交付归因；Bug assignee 是回归负责人，按原字段语义展示。
- 每周简报为选中日期所在周的周一至所选日期；历史数据按日期截止，不把未来事件计入。

## 运行

```sh
python3 -m unittest -v test_board.py
python3 app.py --host 127.0.0.1 --port 9090
```

在具备 Node.js 的开发机可运行 `node test_frontend.cjs`，连接已部署服务检查页面渲染路径与真实 API，只创建并注销测试会话，不写业务工作数据。该检查不替代浏览器截图验收。

生产由用户级 `progress-board.service` 管理，仅操作这个新服务。目录 `/home/cardxu/progress-board`。
部署前确认 9090 空闲，创建 data 目录；复制 unit 至 `~/.config/systemd/user/`，执行用户级 daemon-reload、enable、start。
更新时先备份本服务源码，保留 data；仅重启 progress-board.service。回滚恢复上一版源码后重启本服务。
备份使用 `sqlite3.Connection.backup`，避免直接复制仍在写入的 WAL 数据库。

## 页面与接口

- 首页统计计划、新增、完成、当前待处理；异常默认一行，点击展开；今日完成位于成员工作下。
- 每日 / 每周简报；成员工作与可追溯进度；需求提交记录自动归集；持久意见反馈。
- 总报记录：累计展示截至所选日期的已完成工作，按完成时间倒序；支持搜索、每页 30 项及证据回溯。未完成和非 Bug / 重复归档不计入。
- 登录后在“Agent 接口”创建密钥，示例与接口地址在页面内查看。
- `POST /api/work-items` 创建工作，`POST /api/work-items/{id}/events` 更新子项 / 状态 / 计划。
- Agent 写入必须带 Bearer 密钥和 Idempotency-Key；相同键和不同内容返回 409。
- 子项状态 pending / processed / verified；完成工作必须全部子项 verified。
- 已处理 / 已验收须填写记录或证据 URL；仅允许 http/https 链接。
- `GET /api/dashboard?day=YYYY-MM-DD&period=week` 汇总；`GET /api/items/{id}?day=YYYY-MM-DD` 详情。
- `GET /healthz` 用于进程检查；业务源读取失败返回 503，页面保留原数据并提示。

运行日志不记录密钥、Cookie 或请求体。页面文字均转义，静态文件只暴露三个固定路由。
