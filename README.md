# 基于 MongoDB 的在线问卷系统

课程大作业：一个简化版的在线问卷系统（类似问卷星 / Google Forms）。
用户注册登录后可以创建问卷、添加四种题型、配置跳转规则、发布链接；
其他登录用户可以通过链接填写；创建者可以查看自动生成的统计结果。

> 代码仓库：<https://github.com/xxxsheepkk/ECNU-PJ1-survey-system>
>
> 本仓库包含两个阶段的实现。
> - **第一阶段**（2026-10-21）：问卷创建、四种题型、跳转、发布填写、统计。
> - **第二阶段**（2026-10-29，按「需求变更」完成）：题目库与题目复用、题目共享、
>   版本化与修改历史、发布后冻结、题库、单题跨问卷统计。
>
> 本地早期项目 `Project1` 仅作过程留档，提交与评审以本仓库为准。
> 实现过程、命令和测试结果记录在 [docs/AI_LOG.md](docs/AI_LOG.md)。

- 后端：Python 3 + Flask 3
- 数据库：MongoDB 8（PyMongo）
- 前端：Jinja2 服务端渲染 + 原生 HTML/CSS/JavaScript
- 测试：pytest + requests 真实 HTTP

## 功能

| 模块 | 功能 |
| --- | --- |
| 用户 | 注册、登录、退出；每个用户只能看到和管理自己创建的问卷 |
| 问卷 | 标题、说明、匿名开关、重复提交开关、截止时间、发布、关闭、公开链接 |
| 题型 | 单选、多选（最少/最多选择数）、文本（字数限制）、数字（范围、整数限制） |
| 校验 | 必答、选项合法性、多选数量、文本长度、数字范围/整数、服务端二次校验 |
| 跳转 | 单选按选项、多选按选项/选择数量、数字按大小比较；目标为后续题目或结束问卷 |
| 填写 | 登录后填写、逐题保存草稿、按规则跳转、提交前检查、匿名提交、重复提交控制 |
| 统计 | 单选/多选计数与占比、文本答案分页、数字平均值/最小值/最大值、整卷与单题统计 |
| 题目库 | 题目独立保存，可被多个问卷复用；编辑生成新版本，旧版本与已发布问卷不受影响 |
| 版本 | 版本链（v1→v2→v3）、来源追踪、按内容指纹识别「恢复」，可恢复任一旧版本 |
| 派生 | 基于一题派生独立新题；不同问卷可使用同一题的不同版本 |
| 共享 | 把题目共享给指定用户（跟随最新版本 / 固定版本）；可撤销；已引用问卷不受影响 |
| 题库 | 自建题库、把题目加入题库、从题库选题、更新题库条目版本 |
| 跨问卷统计 | 查看某道题在所有问卷中的回答，按版本分层并按选项身份合并 |
| 安全 | 密码哈希、会话 Cookie、CSRF Token、服务端校验、归属校验、模板自动转义 |

## 快速开始

### 1. 启动 MongoDB

如果 MongoDB 已注册为服务：

```powershell
net start MongoDB
```

否则手动启动（路径按本机情况调整）：

```powershell
New-Item -ItemType Directory -Force D:\MongoDB\data\db | Out-Null
D:\MongoDB\bin\mongod.exe --dbpath D:\MongoDB\data\db --bind_ip 127.0.0.1 --port 27017 --logpath D:\MongoDB\data\mongod.log
```

默认连接 `mongodb://127.0.0.1:27017`，数据库名 `survey_rebuild_db`。

### 2. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

### 3. 启动应用

```powershell
python app.py
```

打开 <http://127.0.0.1:5000>。如果 5000 端口被占用：

```powershell
$env:PORT = "5001"
python app.py
```

### 4. 运行测试

```powershell
python -m pytest tests -q
```

当前结果：

```text
27 passed
```

测试会为每个用例创建独立的 `survey_rebuild_test_<随机串>` 数据库，结束后删除，
不会污染开发数据库。

真实 HTTP 端到端测试需要先启动应用，再运行：

```powershell
$env:BASE_URL = "http://127.0.0.1:5001"
python tools/e2e_smoke.py
```

当前结果：23 项 PASS，输出「E2E 冒烟测试全部通过（23 项）」。

## 配置

配置集中在 [config.py](config.py)，可通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 开发用默认值 | 会话签名与匿名去重密钥；生产环境必须修改 |
| `MONGO_URI` | `mongodb://127.0.0.1:27017` | MongoDB 连接串 |
| `MONGO_DB` | `survey_rebuild_db` | 数据库名 |
| `PORT` | `5000` | 应用监听端口 |
| `FLASK_DEBUG` | `0` | `1` 开启调试模式 |
| `STATS_ANSWER_LIMIT` | `500`（代码内） | 单题统计每页展示的文本/数字答案数 |

## 目录结构

```text
Project1_rebuild/
├── app.py                  # 应用工厂、CSRF、模板过滤器、错误页
├── config.py               # 配置
├── db.py                   # MongoDB 连接与索引
├── auth.py                 # 注册/登录/退出、登录态
├── surveys.py              # 问卷、问卷题目引用、跳转规则管理
├── library.py              # 题目库：题目族、版本、派生、恢复、共享
├── banks.py                # 题库：题库与题目条目管理
├── question_forms.py       # 题目内容表单解析与回显（问卷与题目库共用）
├── fill.py                 # 填写流程（开始/答题/检查/提交/放弃）
├── stats.py                # 统计页面
├── logic.py                # 答案校验、跳转规则校验与执行
├── stats_service.py        # MongoDB 聚合统计（单问卷 + 跨问卷）
├── utils.py                # 时间、类型、展示、opt_id、内容指纹等工具
├── templates/              # Jinja2 页面（surveys / fill / stats / library / banks / auth）
├── static/                 # CSS
├── tests/                  # pytest 测试（含 test_phase2.py）
├── tools/
│   └── e2e_smoke.py        # 真实 HTTP 端到端测试
└── docs/                   # 设计、API、测试、报告、AI 日志、提示词、答辩
```

## 关键设计

- **题目三锚点建模**：题目族 `questions`（逻辑身份）/ 不可变版本 `question_versions`
  （内容快照）/ 问卷引用 `survey_questions`（指向某版本 + 平铺冻结快照 + 跳转规则）。
  问卷不再「拥有」题目，而是「引用」题目，因此一题可被多问卷复用。
- **选项身份稳定（opt_id）**：每个选项有全局稳定的 `opt_id`；答案、跳转规则都存
  `opt_id` 而非字母 key，跨版本修改后统计仍能按同一选项合并。
- **版本不可变 + 发布冻结**：编辑题目总是新增版本；问卷发布后 `survey_questions.locked=true`，
  已发布问卷只引用旧版本快照，改题不可能改动历史答卷。
- **版本关系可追溯**：`derived_from_version_id` 记录来源；按 `content_hash` 相等识别
  「恢复」边与等价版本组；派生（fork）得到独立题目族。
- **跳转规则数据化**：规则保存在 `survey_questions.jump_rules` 中，由后端解释执行，
  不在代码里写死某道题或某个选项；规则按添加顺序匹配，第一条命中生效。
- **只允许向后跳转**：目标必须是当前题目之后的题目或「结束问卷」，
  从数据结构上避免循环跳转和死循环。
- **发布后锁定题目结构**：问卷发布后不能增删改题目和跳转规则，避免已有答卷
  因题目结构变化而失真；仍可修改截止时间和关闭问卷。
- **统计实时聚合**：不在问卷/题目上保存计数字段，统计时对 `responses` 做聚合，
  避免冗余字段与并发更新不一致。
- **匿名与防重复**：匿名提交会移除 `respondent_id`，同时保存一个基于
  用户 ID + 问卷 ID 的 HMAC 去重标识，既保护身份又能实现「每人限一次」。
- **逐题保存**：每次作答立即更新草稿，刷新或中断后可以继续。

## 已知限制

- 文本填空题暂不支持跳转（课程要求只要求单选、多选、数字）。
- 跳转只能向后，暂不支持回跳或循环。
- 问卷发布后题目结构锁定；如需改题，应在题目库生成新版本，或派生新题目后用新版本建新问卷。
- 共享题目默认只允许查看与使用，不能由被共享方直接编辑；共享方可选择「跟随最新」或「固定版本」。
- 第二阶段沿用 MongoDB；应用层实现发布冻结（MongoDB 无触发器）。
- 未实现答案导出、图形化报表和邮件通知。
- 部署时请使用生产级 WSGI 服务器，并设置强随机 `SECRET_KEY`。

## 提交材料对应关系

| 要求 | 位置 |
| --- | --- |
| 后端代码 / MongoDB schema / 配置 | 本仓库源码、[docs/DESIGN.md](docs/DESIGN.md) |
| 系统说明、数据库设计、API、关键逻辑 | [README.md](README.md)、[docs/DESIGN.md](docs/DESIGN.md)、[docs/API.md](docs/API.md) |
| 测试用例（步骤/输入/输出/结果） | [docs/TESTING.md](docs/TESTING.md)、`tests/` |
| 项目完成报告 | [docs/REPORT.md](docs/REPORT.md) |
| AI 使用日志与说明 | [docs/AI_LOG.md](docs/AI_LOG.md) |
| 分步提示词脚本 | [docs/PROMPTS.md](docs/PROMPTS.md) |
| 答辩准备 | [docs/DEFENSE.md](docs/DEFENSE.md) |
