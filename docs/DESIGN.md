# 系统设计说明

## 1. 需求与实现映射

| 课程要求 | 实现位置 |
| --- | --- |
| 用户注册、登录；只能看自己的问卷 | `auth.py`、`surveys.py:_owned_survey_or_404` |
| 问卷标题、说明、匿名、发布、截止、关闭、公开链接 | `surveys.py`、`templates/surveys/` |
| 单选、多选（最少/最多）、文本（字数）、数字（范围/整数） | `surveys.py:_parse_question_form`、`logic.py:normalize_answer` |
| 必答 | `normalize_answer`、题目表单 |
| 单选/多选/数字可配置跳转，不能硬编码 | `questions.jump_rules`、`logic.py:evaluate_next_question` |
| 逐题填写、跳转、错误提示、每题保存 | `fill.py`、`templates/fill/question.html` |
| 并发开始填写、匿名、重复提交 | 部分唯一索引、HMAC `respondent_key`、`fill.py` |
| 单选/多选计数、文本列表、数字平均、整卷/单题统计 | `stats_service.py`、`stats.py` |
| 多集合 MongoDB、减少冗余、说明理由 | 本文第 3、4 节 |
| 测试覆盖创建、加题、跳转、校验、提交、统计 | `tests/`、[TESTING.md](TESTING.md) |
| 交付代码、文档、测试、报告、AI 日志 | 本目录 |

## 2. 总体架构

```text
浏览器
  │  HTTP + Cookie + CSRF
  ▼
Flask 应用工厂 app.py
  ├── auth.py        认证与会话
  ├── surveys.py     问卷/题目/规则管理（创建者）
  ├── fill.py        填写流程（回答者）
  ├── stats.py       统计页面（创建者）
  ├── logic.py       答案校验与跳转解释
  ├── stats_service.py  聚合查询
  └── db.py          MongoDB 连接与索引
  │
  ▼
MongoDB
  users / surveys / questions / responses
```

服务端渲染 Jinja2 页面，不依赖前端框架；所有业务规则在服务端执行，
HTML 的 `required`、`min`、`max` 只用于改善输入体验。

## 3. 数据模型

### 3.1 users

```json
{
  "_id": ObjectId("..."),
  "username": "alice",
  "password_hash": "scrypt:...",
  "created_at": ISODate("2026-10-21T02:00:00Z")
}
```

- `username` 唯一索引；
- 只保存密码哈希，不保存明文。

### 3.2 surveys

```json
{
  "_id": ObjectId("..."),
  "owner_id": ObjectId("..."),
  "public_id": "x7k2mQ9a",
  "title": "课程反馈",
  "description": "请如实填写",
  "allow_anonymous": true,
  "allow_multiple": true,
  "deadline": ISODate("2026-11-01T15:59:00Z"),
  "status": "published",
  "created_at": ISODate("..."),
  "updated_at": ISODate("..."),
  "published_at": ISODate("..."),
  "closed_at": null
}
```

- `public_id` 是公开链接中的短标识，唯一索引；
- `owner_id` 引用 `users._id`，用于权限隔离；
- `status` 为 `draft | published | closed`；
- `deadline` 使用 UTC 存储，展示时转本地时间。

### 3.3 questions

```json
{
  "_id": ObjectId("..."),
  "survey_id": ObjectId("..."),
  "order": 1,
  "type": "multiple",
  "title": "你喜欢哪些水果？",
  "required": true,
  "options": [
    {"key": "a", "text": "苹果"},
    {"key": "b", "text": "香蕉"},
    {"key": "c", "text": "西瓜"}
  ],
  "settings": {
    "min_selections": 1,
    "max_selections": 2
  },
  "jump_rules": [
    {
      "condition": {"operator": "contains", "value": "c"},
      "target": "end"
    }
  ],
  "created_at": ISODate("..."),
  "updated_at": ISODate("...")
}
```

- `options` 与 `jump_rules` 内嵌在题目文档中；
- `settings` 按题型保存不同字段：
  - 多选：`min_selections`、`max_selections`；
  - 文本：`min_length`、`max_length`；
  - 数字：`min`、`max`、`integer_only`；
- `(survey_id, order)` 唯一索引，保证题目顺序稳定；
- 选项 `key` 是稳定标识，规则引用 `key` 而不是选项文本或位置。

### 3.4 responses

```json
{
  "_id": ObjectId("..."),
  "survey_id": ObjectId("..."),
  "respondent_id": ObjectId("..."),
  "anonymous": false,
  "status": "submitted",
  "answers": [
    {"question_id": ObjectId("..."), "type": "single", "value": "a", "answered_at": ISODate("...")},
    {"question_id": ObjectId("..."), "type": "text", "value": "hello", "answered_at": ISODate("...")}
  ],
  "path": [ObjectId("..."), ObjectId("...")],
  "current_question_id": null,
  "started_at": ISODate("..."),
  "updated_at": ISODate("..."),
  "submitted_at": ISODate("...")
}
```

- 一份答卷的 `answers` 与 `path` 内嵌；
- `path` 记录实际经过的题目顺序，跳转后的路径可直接审计；
- 草稿 `status=in_progress`，提交后 `status=submitted`；
- 匿名提交时删除 `respondent_id`，写入 HMAC `respondent_key`。

## 4. 内嵌与引用的理由

| 数据 | 方式 | 理由 |
| --- | --- | --- |
| 题目选项 | 内嵌在 question | 总是与题目一起读写；选项数量有限；避免每次查询再关联 |
| 跳转规则 | 内嵌在 question | 规则只属于该题；保存/发布时整题校验；按顺序匹配方便 |
| 答卷答案 | 内嵌在 response | 一次填写的所有答案总是一起读取、提交、统计；减少关联 |
| 实际路径 | 内嵌在 response | 与答案同生命周期；统计和审计时一起读取 |
| `owner_id` | 引用 user | 用户信息独立变化；问卷列表按 owner 查询 |
| `respondent_id` | 引用 user | 实名答卷需要关联用户；匿名时删除该字段 |
| `survey_id` | 引用 survey | 题目和答卷数量可能增长；按问卷查询和统计 |

没有把所有数据放进一个集合，也没有把每个选项/每条答案拆成独立集合：
前者会导致文档过大、更新竞争和权限难控；后者会导致大量关联查询。

## 5. 索引设计

| 索引 | 作用 |
| --- | --- |
| `users.username` 唯一 | 注册时防止重复用户名，登录按用户名查找 |
| `surveys.public_id` 唯一 | 公开链接定位问卷，防止短链接冲突 |
| `surveys(owner_id, created_at desc)` | 创建者首页按时间列出自己的问卷 |
| `questions(survey_id, order)` 唯一 | 按顺序读取题目；防止同一问卷出现重复顺序 |
| `responses(survey_id, status)` | 统计已提交答卷、检查问卷答卷数 |
| `responses(survey_id, respondent_id, status)` | 查找用户在某问卷下的草稿/提交 |
| `responses(survey_id, respondent_key, status)` | 匿名答卷按去重标识查询 |
| `responses.answers.question_id` 多键 | 统计时按题目筛选答案 |
| `(survey_id, respondent_id)` 部分唯一，条件 `status=in_progress` | 同一用户同一问卷最多一份草稿；已提交答卷不受唯一约束 |

## 6. 问卷生命周期

```text
draft ──发布──▶ published ──关闭──▶ closed
  │                  │
  │                  └── 截止时间到达后拒绝开始/继续
  └── 可增删改题目、配置规则
```

- `draft`：可编辑问卷信息、题目、规则；
- `published`：题目结构锁定；可修改截止时间、关闭；
- `closed`：拒绝新的填写和继续填写；
- 发布前检查：至少一道题；所有跳转规则目标存在且在当前题目之后。

## 7. 跳转设计

### 7.1 规则结构

```json
{
  "condition": {"operator": "gte", "value": 18},
  "target": "end"
}
```

支持的运算符：

| 题型 | 运算符 | 含义 |
| --- | --- | --- |
| 单选 | `equals` / `not_equals` | 等于 / 不等于某选项 |
| 多选 | `contains` / `not_contains` | 包含 / 不包含某选项 |
| 多选 | `count_gte` / `count_lte` / `count_eq` | 选择数量比较 |
| 数字 | `lt` / `lte` / `eq` / `gte` / `gt` | 数值比较 |

### 7.2 匹配算法

```text
for rule in question.jump_rules:        # 按保存顺序
    if rule_matches(question, rule, value):
        if rule.target == "end": return None
        return 目标题目（必须 order 更大）
return 下一道 order 更大的题目
```

- 第一条命中的规则生效；
- 目标只能是后面的题目或 `end`，从结构上避免循环；
- 目标在保存时校验，发布前再次全量校验；
- 代码中没有任何 `if 第1题选A` 之类的硬编码。

### 7.3 路径记录

每次作答把当前题目追加到 `path`，并计算 `current_question_id`。
提交时按 `path` 校验答案，因此被跳过的必答题不会被误判为缺失。

## 8. 校验设计

三层：

1. **HTML 约束**：`required`、`minlength`、`maxlength`、`min`、`max`、`type=number`；
2. **服务端答案校验**：`logic.normalize_answer`，所有填写提交都经过；
3. **业务状态校验**：问卷是否发布/关闭/过期、是否重复提交、是否越权。

主要规则：

| 场景 | 规则 |
| --- | --- |
| 单选 | 必答时不能为空；值必须是已有选项 key |
| 多选 | 去重、按选项顺序保存；满足 min/max；不能包含不存在的 key |
| 文本 | 必答时不能全空白；有内容时检查 min/max 长度 |
| 数字 | 必须可解析；`integer_only` 时不能有小数点/指数；检查 min/max；拒绝 NaN/Infinity |
| 题目配置 | 选择题至少 2 项、最多 20 项、不能重复；多选 min/max 不能矛盾，且不能超过选项总数；文本 min/max 不能矛盾；数字 min/max 不能矛盾 |
| 跳转规则 | 题型支持该运算符；选项条件必须存在；数量/数值条件必须合法；目标必须存在且在当前题目之后 |
| 发布 | 至少一道题；所有规则通过全量校验 |
| 填写 | 问卷已发布、未关闭、未过期；不允许重复提交时检查已提交记录 |
| 权限 | 创建者只能操作自己的问卷；越权返回 404 |

`normalize_answer` 返回规范化值和错误信息；调用方决定 HTTP 状态码和页面提示。

## 9. 填写、草稿与并发

### 9.1 草稿

- 开始填写时插入 `responses`，`status=in_progress`；
- 每次作答立即更新 `answers`、`path`、`current_question_id`；
- 刷新页面会从数据库恢复草稿；
- 放弃时删除草稿；
- 提交时用条件更新 `{"_id": draft_id, "status": "in_progress"}`，避免重复提交。

### 9.2 并发

- `(survey_id, respondent_id)` 部分唯一索引保证同一用户同一问卷最多一份草稿；
- 并发点击“开始”时，第二个插入会触发 `DuplicateKeyError`，代码恢复已有草稿；
- 提交使用条件更新，只有一个请求能把 `in_progress` 改为 `submitted`。

### 9.3 匿名与重复提交

- 匿名是问卷级开关；开始时用户勾选；
- 提交时删除 `respondent_id`，设置 `anonymous=true`；
- 同时写入 `respondent_key = HMAC(SECRET_KEY, user_id + survey_id)`；
- 检查重复提交时同时查 `respondent_id` 和 `respondent_key`；
- 这样匿名答卷无法反查用户，但“每人限一次”仍然有效；
- 更换 `SECRET_KEY` 会使旧去重标识失效，这是可接受的折中。

## 10. 统计设计

统计不保存冗余计数字段，查询时对 `responses` 聚合：

- 只匹配 `status=submitted`，草稿不计入；
- 单选：`$unwind answers` → 匹配题目 → 按 `answers.value` 分组计数；
- 多选：在单选管道上再 `$unwind answers.value`，每个选项分别计数；
- 文本：按 `submitted_at` 倒序，`$skip` + `$limit` 分页；
- 数字：`$avg`、`$min`、`$max`、`$sum`，并分页返回回答明细；
- 百分比分母是“实际回答该题的人数”，不是总提交数；
- 文本/数字分页每页默认 `STATS_ANSWER_LIMIT=500`，页码超出范围回退到最后一页。

## 11. 安全设计

- 密码使用 Werkzeug `generate_password_hash` / `check_password_hash`；
- 会话 Cookie `HttpOnly`、`SameSite=Lax`；
- 所有 POST/PUT/PATCH/DELETE 校验 CSRF Token（测试中可关闭）；
- 创建者资源查询同时带 `_id` 和 `owner_id`，越权返回 404；
- 模板自动转义，避免 XSS；
- 所有查询使用参数化文档，不拼接用户输入；
- 登录后 `session.clear()` 再写入用户 ID，降低会话固定风险；
- `SECRET_KEY` 生产环境必须通过环境变量设置。

## 12. 第二阶段扩展点

- 题目版本化：发布后修改题目时保留历史版本，答卷引用版本号；
- 支持回跳/循环：需要增加最大步数、访问次数限制和路径校验；
- 答案导出：按 `responses` 聚合生成 CSV/Excel；
- 图形化报表：在统计页增加图表；
- 多语言、邮件通知、问卷模板；
- 生产部署：Gunicorn/Waitress + 反向代理 + MongoDB 副本集。
