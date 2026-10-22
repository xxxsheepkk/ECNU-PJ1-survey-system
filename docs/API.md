# 接口说明

所有页面默认返回 HTML；未登录访问受保护页面会 `302` 到 `/login`。
所有 POST 请求需要表单字段 `_csrf`（测试配置可关闭）。

## 1. 认证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/register` | 注册；字段 `username`、`password`、`confirm_password` |
| GET/POST | `/login` | 登录；字段 `username`、`password`、可选 `next` |
| POST | `/logout` | 退出并清空 session |

注册成功后自动登录并跳转 `/`；登录失败返回 200 并显示错误提示。
`next` 只接受站内相对路径，防止开放重定向。

## 2. 问卷管理（需登录，仅创建者）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 我的问卷列表 |
| GET/POST | `/surveys/new` | 新建问卷 |
| GET/POST | `/surveys/<survey_id>/edit` | 编辑问卷（仅草稿） |
| POST | `/surveys/<survey_id>/deadline` | 更新截止时间 |
| GET | `/surveys/<survey_id>` | 问卷详情与管理页 |
| POST | `/surveys/<survey_id>/publish` | 发布问卷 |
| POST | `/surveys/<survey_id>/close` | 关闭问卷 |

新建/编辑字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 必填，最多 120 字符 |
| `description` | string | 可选，最多 5000 字符 |
| `allow_anonymous` | checkbox | 允许匿名提交 |
| `allow_multiple` | checkbox | 允许重复提交 |
| `deadline` | datetime-local | 可选；按服务器本地时区解析 |

发布条件：至少一道题；所有跳转规则合法。发布后题目结构锁定。

## 3. 题目与跳转规则

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/surveys/<survey_id>/questions/new` | 添加题目（仅草稿） |
| GET/POST | `/surveys/<survey_id>/questions/<question_id>/edit` | 编辑题目（仅草稿，题型不变） |
| POST | `/surveys/<survey_id>/questions/<question_id>/delete` | 删除题目并清理规则 |
| POST | `/surveys/<survey_id>/questions/<question_id>/rules` | 添加跳转规则 |
| POST | `/surveys/<survey_id>/questions/<question_id>/rules/<rule_index>/delete` | 删除第 index 条规则 |

题目字段：

| 字段 | 说明 |
| --- | --- |
| `type` | `single` / `multiple` / `text` / `number` |
| `title` | 题目内容，必填 |
| `required` | 是否必答 |
| `options` | 单选/多选：每行一个选项 |
| `min_selections` / `max_selections` | 多选数量限制 |
| `min_length` / `max_length` | 文本长度限制 |
| `min_value` / `max_value` | 数字范围 |
| `integer_only` | 数字是否只允许整数 |

跳转规则字段：`operator`、`value`、`target`。
`target` 为后续题目的 ObjectId 字符串，或 `end`。
规则按添加顺序匹配，第一条命中生效。

## 4. 填写（需登录）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/survey/<public_id>` | 填写说明页；显示开始/继续/不可填原因 |
| POST | `/survey/<public_id>/start` | 开始填写；可选 `anonymous=1` |
| GET | `/survey/<public_id>/fill` | 当前题目 |
| POST | `/survey/<public_id>/fill/answer` | 提交当前题答案 |
| GET | `/survey/<public_id>/review` | 检查页 |
| POST | `/survey/<public_id>/review/submit` | 提交问卷 |
| GET | `/survey/<public_id>/done` | 提交成功页 |
| POST | `/survey/<public_id>/abandon` | 放弃草稿 |

作答字段：

- `question_id`：当前题目 ID，必须与服务端当前题一致，否则忽略；
- `value`：单选/文本/数字为字符串；多选可重复提交多个 `value`。

提交时只校验 `path` 中实际经过的题目，跳过的必答题不会被误判。

## 5. 统计（需登录，仅创建者）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/surveys/<survey_id>/stats` | 整卷统计总览 |
| GET | `/surveys/<survey_id>/stats/questions/<question_id>` | 单题统计 |

单题统计支持 `?page=N`：

- 文本题：回答内容及提交时间，分页展示；
- 数字题：回答数、平均、最小、最大 + 回答明细分页；
- 单选/多选：每个选项的计数与占比；
- 每页最多 `STATS_ANSWER_LIMIT` 条（默认 500）；
- 页码超出范围时自动回退到最后一页；
- 占比分母为实际回答该题的人数；
- 草稿不计入统计。

## 6. 错误响应

| 状态码 | 场景 |
| --- | --- |
| 400 | 表单校验失败、CSRF 失败、答案不合法 |
| 403 | 无权限 |
| 404 | 资源不存在，或当前用户无权访问 |
| 500 | 服务器内部错误 |

越权访问统一返回 404，避免泄露资源是否存在。
