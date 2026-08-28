# 错误码登记表（Error Codes）

> 本文件是错误码的**唯一真相源**。`CLAUDE.md` 约定业务 `code` 全局唯一并维护于此。
> 新增/修改错误码必须同步更新本表，否则视为契约破坏。
> 状态：2026-08 · 对齐 `scripts/serve.py` 与 `plugins/.../error-map.ts` 实际实现。

---

## 一、JSON-RPC 传输层错误码（`serve.py` 返回）

| code | 含义 | 触发场景 |
|------|------|----------|
| `-32700` | Parse error | 请求行不是合法 JSON |
| `-32600` | Invalid Request / 鉴权拒绝 | 写操作被 `--readonly` 或 `--token` 拒绝，或请求结构非法 |
| `-32601` | Method not found | 调用白名单外/不存在的方法 |

## 二、域错误码（业务语义，`DomainError`）

> 统一以 `code: message` 形式写入 JSON-RPC `error.message`（错误码 `-32001` 外壳）。

| code | 含义 | 触发场景 | 对应文件 |
|------|------|----------|----------|
| `NOT_FOUND` | 资源不存在 | `lookup_exact` 精确查询无匹配节点 | `serve.py` / `error-map.ts` |
| `OVERLAP_REJECTED` | 重叠率超阈值 | `routing_split` 被重叠门禁拒绝 | `serve.py` / `error-map.ts` |
| `INVALID_INPUT` | 输入非法 | 参数校验失败（预留） | `error-map.ts` |
| `INFRA` | 基础设施故障 | RPC 超时 / 子进程崩溃（TS 侧 `throw`，非域值） | `error-map.ts` |

## 三、通用异常（未分类错误）

| code | 含义 |
|------|------|
| `-32000` | Server error：未捕获异常（`traceback` 已打印） |

## 四、RPC 方法分组（`serve.py`）

读方法（对标 GET，无副作用，始终放行）：
`stats` / `lookup_exact` / `lookup_fuzzy` / `routing_query` / `routing_rank` / `health`

写方法（对标 POST/PUT/DELETE，受 `--readonly` / `--token` 约束）：
`init` / `report_unknown` / `planner_plan` / `routing_split` / `routing_prune`

---

## 维护规则

- **新增业务 `code`**：在本表第二节登记 → 在 `error-map.ts` 的 `DOMAIN_ERROR_CODES` 添加 → 在 `serve.py` 抛出 `DomainError`。
- **破坏性变更**（删除/重命名 code）：需同步更新本表 + `error-map.ts`，并保持 `INFRA` 为 `throw` 路径不并入域值。