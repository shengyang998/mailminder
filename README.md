# Mailminder

把邮箱里必须记住的日期，自动变成手机日历里带提醒的日程。

*English: Reads your mailbox and turns the dates you can't afford to miss into calendar reminders on your phone.*

## 它会做什么

- 新邮件到达后，交给一个无头的编程 Agent（Claude Code 或 Codex，你选）读一遍；模型运行时所有工具都被禁用，只能按固定格式回答邮件里有哪些日期。
- 时间的换算（时区、夏令时）全部在代码里用时区数据库完成，不靠模型。
- 换算好的时间写成带提醒的日历事件，出现在手机的「日历」App 里，自动同步。
- 默认每 30 分钟在后台查一次新邮件；第一次运行只往回看 7 天，之后每次只看新邮件。同一封邮件只让模型读一次，答案会缓存，不会重复消耗模型额度。
- 连续 3 次运行失败后，会在日历上写一条「Mailminder 出错了」的提醒（每天最多一条），因为后台任务能触达手机的渠道只有日历。

## 需要什么

- macOS。
- 已安装并登录 Claude Code 或 Codex 其中一个。
- 邮箱和日历要用的密码：
  - iCloud：登录 https://account.apple.com →「登录和安全」→「App 专用密码」生成一个（需要账户已开启双重认证）。这一个密码邮箱和日历共用。
  - Gmail：https://myaccount.google.com/apppasswords 生成应用专用密码（先在 Google 账号开两步验证）。
  - QQ / 163 邮箱：网页版设置里开启 IMAP 服务，生成「授权码」。
  - Gmail / QQ / 163 的服务器设置按各家公开文档预置，目前只在 iCloud 上实测过；遇到问题可以选「其他邮箱」手动填。
- 如果这台 Mac 本身登录着 iCloud，设置向导会自动识别账户、邮箱地址和服务器，只需要粘贴密码。

## 安装

```
git clone https://github.com/shengyang998/mailminder.git && cd mailminder && ./install.sh
```

`install.sh` 会检查是不是 macOS、检查 Claude Code 或 Codex 至少装了一个（都没有会提示怎么装并退出），缺 `uv` 就先装 `uv`，把 `mailminder` 命令放进 PATH，然后启动设置向导。向导共 5 步，每一步之后都能用命令单独改：

1. **AI** — 选 Claude Code 还是 Codex、选模型，试跑一次确认已登录、能正常回答。
2. **邮箱** — 选 iCloud / Gmail / QQ / 163 邮箱 / 其他 IMAP 服务器，粘贴密码。
3. **日历** — 选 iCloud 日历还是其他 CalDAV 服务器；推荐新建一个专用日历「邮件提醒」，也可以选写进已有日历。
4. **时区和频率** — 时区默认是这台 Mac 当前的系统时区，可以改；再设查邮件的间隔（默认 30 分钟）和第一次往回看的天数（默认 7 天）。
5. **试运行** — 先只读不写，列出会新建哪些日程，确认后才真正写入日历；不确认就放弃这一批，之后只处理新邮件。写完之后安装后台任务，并在 launchd 里跑一次自检——和真正定时运行一样没有终端、权限精简——设置的时候就能看到权限问题，而不是等到半夜任务失败才发现。

不需要任何 macOS 隐私权限（完整磁盘访问、日历、邮件），因为它直接用 IMAP 和 CalDAV 协议连服务器，不经过 Mail.app 或「日历」App。

## 常用命令

| 命令 | 作用 |
|---|---|
| `mailminder status` | 看当前设置、后台任务状态、最近几次运行、接下来的日程 |
| `mailminder run [--dry-run] [--since 天] [--limit 封]` | 立刻查一次新邮件；`--dry-run` 只看不写；`--since` 重新扫最近 N 天；`--limit` 这次最多读几封 |
| `mailminder model [名字] [--harness claude\|codex] [--effort low\|medium\|high]` | 查看或更换 AI 和模型，例如 `mailminder model sonnet`、`mailminder model --harness codex gpt-6-astra`、`mailminder model --effort low` |
| `mailminder account [add\|remove\|password\|folders]` | 管理邮箱账号、要读的文件夹、App 专用密码；不带参数列出已有账号 |
| `mailminder config [show\|set 键 值]` | 查看或修改设置；可设置：`timezone` `language` `interval` `lookback` `max-per-run` `batch-size` `effort` `timeout` |
| `mailminder doctor [--write-test]` | 逐项检查模型、邮箱、日历、后台任务；`--write-test` 顺便往日历写一条测试日程再删掉 |
| `mailminder agent install\|uninstall\|selftest` | 单独管理后台任务 |
| `mailminder delete-all [--yes]` | 删掉 Mailminder 建的全部日程；`--yes` 跳过确认 |
| `mailminder uninstall [--purge]` | 卸载后台任务；`--purge` 连密码、配置、运行记录一起删 |

`language` 决定模型写的标题和备注用中文（`zh-Hans`）还是英文（`en`），备注里的固定文字目前只有中文；改 `interval` 会自动重装后台任务，不用手动 `agent install`。

## 时区

- 模型只负责照抄邮件里写的时间，以及邮件明确写出或能明确推出的时区（比如一趟航班用起飞机场所在地的时区）；时区之间的换算全部在代码里用系统的时区数据库完成，不靠模型。
- 邮件没写时区时，按你设置的时区处理，日程备注里会写明用的是你的时区。
- 定时事件在日历里按 UTC 存储，手机自动显示成本地时间；全天的截止日期保持「全天」，不做时区换算。
- 邮件时间所在时区和你的时区不一样时，备注里会同时写出：当地时间（时区）＝ 你的时区时间。
- 夏令时的边界情况也处理了：调快时段里不存在的那个时刻，顺延到调快之后；调慢时段里出现两次的时刻，按第一次、较早的算——不会因为夏令时提醒错时间或漏提醒。

## 提醒规则

| 类型 | 提醒时间 |
|---|---|
| 出行 travel | 提前 1 天、提前 3 小时 |
| 预约 appointment | 提前 1 天、提前 1 小时 |
| 会议 meeting | 提前 30 分钟 |
| 截止 deadline | 提前 1 天、提前 2 小时 |
| 活动 event | 提前 1 天、提前 2 小时 |
| 其他 other | 提前 1 小时 |
| 全天日期 | 前一天 09:00、当天 09:00 |

已经过去的提醒不会设；定时事件如果算下来提醒全部已经过去，会改成事件开始那一刻提醒一次（全天日期没有这一条兜底）。

- 同一件事被多封邮件提到（比如订票确认信和后续提醒信）→ 只建一条日程，靠邮件里的关键信息（订单号、航班号或标题）加上时间点去重。
- 收到取消邮件 → 已建的日程标题前面加上「【已取消】」，提醒全部去掉，日程本身不删除。
- 改期邮件 → 新时间点新建一条日程，原时间点那条标记取消。
- 在手机上手动删掉某条日程之后，Mailminder 不会把它重新建回来。

## 安全与隐私

- 邮箱以只读方式打开（IMAP EXAMINE + BODY.PEEK），未读邮件不会被标记成已读。
- 密码只存在这台 Mac 的登录钥匙串里。
- 模型运行时所有工具都被禁用，必须按固定 JSON 格式回答，邮件正文只当数据处理，不当指令执行：
  - Claude Code：`--restricted --tools ""`，空的 MCP 配置，不保留会话记录。
  - Codex：`--ignore-user-config`，只读沙箱，禁用 shell 相关工具。
  - 就算邮件里藏了提示注入，最坏结果是建错一条日程，模型没有能力执行命令或读写文件。
- 邮件内容会发给你选的模型提供方（Anthropic 或 OpenAI），走的是你自己的账号和订阅。

## 做不到的

- 苹果的「提醒事项」App：iOS 13 之后不再走 CalDAV，写不进去。
- Outlook / Hotmail：微软的 IMAP 需要 OAuth 登录，App 专用密码这条路走不通。
- 把 Google 日历当写入目标：同样需要 OAuth；请用 iCloud 日历。
- 非 macOS 系统：设置向导、后台任务、钥匙串都是 macOS 专属机制。

## 文件在哪

| 内容 | 位置 |
|---|---|
| 配置 | `~/.config/mailminder/config.toml` |
| 运行状态、缓存、日志 | `~/.local/state/mailminder/`（`state.db`、`logs/agent.log`） |
| 后台任务 | `~/Library/LaunchAgents/local.mailminder.plist` |

## 卸载

```
mailminder uninstall --purge
uv tool uninstall mailminder
```

`--purge` 会删掉钥匙串里的密码、配置文件和运行记录；日历「邮件提醒」和里面已经建好的日程不会动，不想要了在「日历」App 里手动删。

## 开发

- 单元测试（不联网）：`uv run pytest`
- 真实环境测试（用已安装的配置，只读方式碰真实邮箱、建一个用完即删的临时日历、真的调用模型）：`MAILMINDER_LIVE=1 uv run pytest`
  - `tests/test_live_extract.py` 是一个 12 封邮件的准确率评测，加 `-s` 能看到每封邮件的判断结果表格。
- 想让配置和运行状态临时指到别处（比如测试时不碰真实配置），设置 `MAILMINDER_HOME` 环境变量。
- 许可证：MIT。
