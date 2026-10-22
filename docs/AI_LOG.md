# AI 使用日志（重新实现版）

## 0. 关于这份日志的真实性

- 本日志记录的是 **2026-10-21 在 `D:\MongoDB\Project1_rebuild` 重新实现**
  课程大作业第一阶段的真实过程。
- 原项目位于 `D:\MongoDB\Project1`，**保留未删除**，其代码、文档和历史记录
  均可查。本次不是“第一次实验”的原始时间线，而是按
  [PROMPTS.md](PROMPTS.md) 的 16 步重新做一遍。
- 日志中的命令、报错、测试数量和输出均来自实际执行；没有把重新实现
  伪造成第一次实验，也没有删除或改写原项目的历史。
- 用户在本过程中的角色：确定“重新实现、保留原项目”的方案；要求日志真实；
  确定只做第一阶段；验收代码与测试结果。AI 的角色：按步骤设计、编码、
  运行测试、修复错误、生成文档。

## 1. 环境

| 项目 | 值 |
| --- | --- |
| AI 工具 | Claude Code（Anthropic 的 AI 编程助手） |
| 日期 | 2026-10-21 |
| 操作系统 | Windows 11 |
| 工作目录 | `D:\MongoDB\Project1_rebuild` |
| Python | 3.14.7 |
| Flask | 3.1.3 |
| PyMongo | 4.18.2 |
| MongoDB | 8.3.11，`mongodb://127.0.0.1:27017` |
| pytest | 9.1.1 |
| requests | 2.34.2 |

## 2. 实际执行步骤

### 第 1 步：读需求

读取 `D:\MongoDB\Project1\大作业一第一阶段.docx`，提取出：

- 用户注册登录，只能管理自己的问卷；
- 问卷字段：标题、说明、匿名、发布、截止、关闭、公开链接；
- 四种题型：单选、多选（最少/最多）、文本（字数）、数字（范围/整数）；
- 必答；
- 单选/多选/数字的可配置跳转；
- 逐题保存、错误提示；
- 匿名、重复提交、并发开始；
- 单选/多选计数、文本列表、数字平均、整卷/单题统计；
- 多集合设计并说明内嵌/引用；
- 测试、报告、AI 日志。

**结果**：形成需求清单，见 [DESIGN.md](DESIGN.md) 第 1 节。

### 第 2 步：锁定范围

只做第一阶段。确定模块：

```text
auth / surveys / fill / stats / logic / stats_service / utils
```

明确不做：题目版本化、回跳、图表、导出、邮件通知。

### 第 3 步：设计集合

确定四个集合：`users`、`surveys`、`questions`、`responses`。

- 选项、跳转规则内嵌题目；
- 答案、实际路径内嵌答卷；
- `owner_id`、`respondent_id`、`survey_id` 使用引用。

**结果**：写入 [DESIGN.md](DESIGN.md) 第 3、4 节。

### 第 4 步：设计索引

在 `db.py` 中实现：

- `users.username` 唯一；
- `surveys.public_id` 唯一；
- `surveys(owner_id, created_at desc)`；
- `questions(survey_id, order)` 唯一；
- `responses(survey_id, status)`；
- `responses(survey_id, respondent_id, status)`；
- `responses(survey_id, respondent_key, status)`；
- `responses.answers.question_id` 多键；
- `(survey_id, respondent_id)` 部分唯一，条件 `status=in_progress`。

关键代码：

```python
db[RESPONSES].create_index(
    [("survey_id", ASCENDING), ("respondent_id", ASCENDING)],
    unique=True,
    partialFilterExpression={"status": "in_progress"},
    name="uq_in_progress_draft",
)
```

### 第 5 步：设计跳转

规则存在 `questions.jump_rules`，按顺序匹配，第一条命中生效；
目标只能是后续题目或 `"end"`。

运算符：

- 单选：`equals`、`not_equals`；
- 多选：`contains`、`not_contains`、`count_gte`、`count_lte`、`count_eq`；
- 数字：`lt`、`lte`、`eq`、`gte`、`gt`。

实现于 `logic.rule_matches`、`logic.evaluate_next_question`、
`logic.validate_jump_rule`。

### 第 6 步：列出校验

实现三层校验：

1. HTML 约束；
2. `logic.normalize_answer` 服务端答案校验；
3. 问卷状态、重复提交、权限等业务校验。

重新实现时补上一条原项目没有的边界：

```python
if low is not None and len(options) and low > len(options):
    errors.append("最少选择数量不能超过选项总数。")
```

避免创建“3 个选项却要求至少选 5 项”的无法作答题目。
对应测试见 `tests/test_validation.py`。

### 第 7 步：搭骨架

创建：

- `config.py`：`SECRET_KEY`、`MONGO_URI`、`MONGO_DB`、`CSRF_ENABLED`、
  `STATS_ANSWER_LIMIT`；
- `db.py`：连接、集合常量、索引；
- `app.py`：应用工厂、CSRF、模板过滤器、错误页；
- `templates/base.html`、`static/style.css`。

启动验证：

```powershell
python app.py
```

访问 `http://127.0.0.1:5000/` 返回 200。

### 第 8 步：认证

`auth.py` 实现注册、登录、退出：

- `generate_password_hash` / `check_password_hash`；
- 登录成功前 `session.clear()`；
- `load_logged_in_user`；
- `login_required`；
- 所有 POST 校验 CSRF。

### 第 9 步：问卷与题目

`surveys.py` 实现：

- 问卷 CRUD、发布、关闭、截止时间；
- 题目增删改，选项内嵌；
- 跳转规则增删；
- 发布后锁定结构；
- 编辑选项时按文本保留旧 key，避免规则错位；
- 删除题目/选项后清理失效规则（`_prune_rules`）；
- 越权统一 404。

### 第 10 步：填写流程

`fill.py` 实现：

- 开始填写创建 `in_progress` 草稿；
- 每次作答保存 `answers`、`path`、`current_question_id`；
- 按规则跳转；
- 检查页、提交、放弃；
- 匿名提交：

```python
update["$set"]["respondent_key"] = respondent_key(
    current_app.config["SECRET_KEY"], g.user["_id"], survey["_id"]
)
update["$unset"]["respondent_id"] = ""
```

- 提交使用条件更新，保证并发下只成功一次；
- `DuplicateKeyError` 时恢复已有草稿。

### 第 11 步：统计

`stats_service.py` 使用聚合：

- `$unwind answers`；
- 多选再 `$unwind answers.value`；
- `$group` 计数；
- 数字 `$avg`、`$min`、`$max`；
- 文本 `$sort` + `$skip` + `$limit` 分页。

`stats.py` 处理权限、页码与越界回退：

```python
total_pages = max(1, math.ceil(stats["answered_count"] / per_page))
if page > total_pages:
    page = total_pages
    stats = load(page)
```

### 第 12 步：pytest

创建 `tests/`：

```text
conftest.py      每个用例独立测试库
helpers.py       注册/建卷/加题/发布/填写封装
test_auth.py
test_surveys.py
test_fill.py
test_validation.py
test_stats.py
test_csrf.py
```

运行：

```powershell
python -m pytest tests -q
```

实际结果：

```text
..................                                                       [100%]
18 passed in 5.41s
```

### 第 13 步：真实 HTTP E2E

创建 `tools/e2e_smoke.py`，使用 `requests.Session` 走真实 Cookie、CSRF、
重定向：

```text
注册 → 建卷 → 加 3 题 → 配单选跳转 → 发布
→ 实名填写（验证跳过第二题）→ 提交
→ 匿名填写（同样跳转）→ 提交
→ 创建者查看统计 → 清理测试数据
```

运行：

```powershell
$env:PORT = "5001"
python app.py
# 另一个终端
$env:BASE_URL = "http://127.0.0.1:5001"
python tools/e2e_smoke.py
```

实际输出：

```text
E2E 冒烟测试全部通过（23 项）。
测试数据已清理。
```

### 第 14 步：文档

生成：

- `README.md`：安装、启动、目录、路由、提交说明；
- `docs/DESIGN.md`：需求映射、架构、数据模型、内嵌/引用、索引、跳转、
  校验、草稿与并发、统计、安全；
- `docs/API.md`：路由、字段、状态码；
- `docs/TESTING.md`：环境、隔离、18 个用例、E2E 输出；
- `docs/REPORT.md`：完成报告；
- `docs/AI_LOG.md`：本文件。

### 第 15 步：自审

逐条对照课程要求检查，发现并处理：

| 发现 | 处理 |
| --- | --- |
| 多选 `min_selections` 可大于选项数 | 增加配置校验和 T11 测试 |
| 文本答案仅显示前 N 条 | 增加分页与页码回退 |
| 发布后仍可能加题 | 发布后锁定结构，测试覆盖 |
| 匿名后无法判断是否提交过 | HMAC `respondent_key` 去重 |

### 第 16 步：答辩准备

基于 `DESIGN.md` 整理 10 个问题：四个集合、内嵌/引用、跳转、匿名去重、
并发、统计分母、发布锁定、校验分层、索引、第二阶段扩展。
每题都能在代码或文档中指出依据，见 [DEFENSE.md](DEFENSE.md)。

## 3. 实际遇到的错误与修复

### 3.1 MongoDB 未启动

**现象**：

```text
pymongo.errors.ServerSelectionTimeoutError:
127.0.0.1:27017: [WinError 10061] 由于目标计算机积极拒绝，无法连接。
```

**修复**：启动本机 MongoDB：

```powershell
D:\MongoDB\bin\mongod.exe --dbpath D:\MongoDB\data\db ^
  --bind_ip 127.0.0.1 --port 27017 ^
  --logpath D:\MongoDB\data\mongod.log
```

之后 pytest 与 E2E 均正常。README 已写明启动方式。

### 3.2 发布后再加题的测试写法错误

**现象**：`test_surveys.py` 先发布，再调用 `add_question` helper；
helper 内部断言题目创建成功，与“发布后锁定”冲突。

**修复**：改为直接 POST 加题，并断言页面提示
「发布后不能增加题目」，题目数仍为 4。

### 3.3 非法多选配置的测试顺序错误

**现象**：`test_validation.py` 原本在发布后提交非法配置，
但发布锁会先拒绝“加题”，测试失败原因不对。

**修复**：把非法配置的 POST 移到 `publish` 之前，再断言
「最少选择数量不能超过选项总数」且未写入数据库。

### 3.4 E2E 文案错别字

**现象**：输出 “匿提交成功”。

**修复**：改为 “匿名提交成功”。

### 3.5 端口冲突

**现象**：E2E 需要同时运行应用与脚本，5000 端口被占用。

**修复**：`app.py` 支持 `PORT` 环境变量，E2E 使用 5001。

### 3.6 测试包导入失败

**现象**：`from tests.helpers import ...` 无法导入。

**修复**：增加 `tests/__init__.py`。

## 4. 最终验证

```text
python -m pytest tests -q
18 passed in 5.12s

python tools/e2e_smoke.py
E2E 冒烟测试全部通过（23 项）。
测试数据已清理。
```

（第 12 步首次运行时为 `18 passed in 5.41s`；耗时随机器负载浮动，
通过数量不变。）

数据库索引、跳转、匿名、重复提交、统计分页均由上述测试覆盖。

## 5. 与原始项目的关系

- 原项目 `D:\MongoDB\Project1` 完整保留，未删除任何文件或历史；
- 本目录是按分步提示词重新实现的独立版本；
- 两份代码结构相似，但本版本补充了多选配置边界校验、统计分页、
  `PORT` 环境变量等改进；
- 如果两份材料同时提交，应说明哪一份是最终版本，避免评审混淆。

## 6. AI 使用声明

本项目的代码、测试和文档由 AI 按用户的分步指令生成，并由用户验收。
AI 没有伪造运行结果：日志中的测试数量、报错信息和命令输出均可在本机复现。
涉及课程学术诚信的部分，用户已知悉这是重新实现，而非第一次实验的原始记录。
