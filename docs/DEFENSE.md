# 答辩问题准备

以下 10 个问题覆盖数据建模、跳转、校验、并发、统计和安全。
每个问题先给回答要点，再指出依据位置。

## 1. 为什么用四个集合，而不是一个集合或更多集合？

- 四类数据生命周期不同：用户、问卷、题目、答卷；
- 全部塞进一个集合会导致文档过大、更新竞争、权限过滤困难；
- 把每个选项/答案拆成集合会导致大量关联查询；
- 当前划分让“按问卷查题目”“按问卷查答卷”“按用户查问卷”都能走索引。

依据：[DESIGN.md](DESIGN.md) 第 3、4 节，`db.py`。

## 2. 哪些数据内嵌，哪些引用？为什么？

- 选项、跳转规则内嵌题目：总是与题目一起读写，数量有限；
- 答案、`path` 内嵌答卷：一次填写的数据同生命周期；
- `owner_id`、`respondent_id`、`survey_id` 引用：被引用对象独立存在，
  且题目/答卷会持续增长。

依据：[DESIGN.md](DESIGN.md) 第 4 节。

## 3. 跳转规则如何做到不硬编码？

- 规则保存在 `questions.jump_rules`；
- 运算符按题型限定，值存选项 key 或数字；
- `logic.evaluate_next_question` 按顺序匹配，第一条命中生效；
- 代码里没有任何“第 1 题选 A 就跳第 4 题”的分支。

依据：`logic.py`，[DESIGN.md](DESIGN.md) 第 7 节。

## 4. 为什么只允许向后跳？

- 从结构上避免循环和死循环；
- 保存规则和发布前都校验目标 `order` 大于当前题；
- 目标还必须是同一问卷中存在的题目，或 `end`。

依据：`logic.validate_jump_rule`、`surveys.py`。

## 5. 匿名提交后，怎么实现“每人限一次”？

- 提交时删除 `respondent_id`，写入
  `respondent_key = HMAC(SECRET_KEY, user_id + survey_id)`；
- 检查重复提交时同时查实名 ID 和匿名 key；
- 数据库无法从 key 反推用户，但同一用户同一问卷的 key 稳定。

依据：`utils.respondent_key`、`fill.py`，[DESIGN.md](DESIGN.md) 第 9.3 节。

## 6. 并发点击“开始填写”会不会创建两份草稿？

- 不会。`(survey_id, respondent_id)` 上有部分唯一索引，
  条件为 `status=in_progress`；
- 第二个插入触发 `DuplicateKeyError`，代码回退到已有草稿；
- 已提交答卷不受该唯一约束影响，允许 `allow_multiple` 时再次填写。

依据：`db.py`、`fill.py`。

## 7. 提交时如何避免重复提交？

- 提交使用条件更新：
  `{"_id": draft_id, "status": "in_progress"}`；
- 只有一个请求能把状态改为 `submitted`，其余匹配不到文档；
- 旧页面提交错误 `question_id` 会被忽略。

依据：`fill.py`。

## 8. 统计百分比的分母是什么？为什么？

- 分母是“实际回答该题的人数”，不是总提交数；
- 跳转会让部分人没经过某题，用总提交数会导致占比之和不为 100%；
- 草稿不计入统计，只统计 `status=submitted`。

依据：`stats_service.py`，[DESIGN.md](DESIGN.md) 第 10 节。

## 9. 服务端校验和前端校验分别负责什么？

- HTML 的 `required`、`min`、`max` 只改善输入体验，可被绕过；
- 服务端 `logic.normalize_answer` 才是可信边界；
- 业务状态（发布、关闭、截止、重复、权限）也在服务端判断。

依据：`logic.py`、`fill.py`，[DESIGN.md](DESIGN.md) 第 8 节。

## 10. 索引为什么这样设计？部分唯一索引的作用是什么？

- 用户名、公开链接唯一：去重和定位；
- `(survey_id, order)` 唯一：题目顺序稳定；
- `(owner_id, created_at desc)`：首页列表；
- 答卷相关索引支持统计、草稿、匿名去重；
- 部分唯一索引只约束 `in_progress`，既防止多草稿，
  又不阻止同一用户提交多份（允许重复提交时）。

依据：`db.py`，[DESIGN.md](DESIGN.md) 第 5 节。

## 附加：如果要做第二阶段，优先做什么？

题目版本化与回跳/循环。前者解决“发布后改题导致旧答卷失真”，
后者需要最大步数、访问次数和路径校验，风险更高，应后做。
