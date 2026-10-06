# 手术知情同意版本与撤回系统

手术方案变化时，确保排期依据的是**患者对当前方案**签署的知情同意，杜绝凭旧同意书安排手术。

## 一键启动

```bash
./start.sh          # 创建/复用 .venv，安装依赖，启动服务
# 或：python run.py（依赖已安装时）
```

打开 http://127.0.0.1:8000 进入前端（排期总览 / 手术详情 / 通知中心）。
首次启动自动建库并写入演示数据，覆盖三种排期状态：已签署有效、方案变更待重签、待签署。

运行验证测试：

```bash
.venv/bin/python -m pytest tests -v
```

## 业务规则

| 规则 | 实现 |
|---|---|
| 同一手术只有一份当前有效版本 | `consent_versions` 上 `(surgery_id) WHERE status='ACTIVE'` 部分唯一索引 + 事务内取代 |
| 关键内容（术式/麻醉/风险）变更后原签署失效 | 新版本入库同事务将旧版本置 `SUPERSEDED`、旧签名置 `INVALID`，排期就绪检查只认 ACTIVE 版本的 VALID 签名 |
| 旧签名不能复制到新版 | ① 仅 ACTIVE 版本可签，旧版本签署返回 409；② 签名回执绑定内容哈希，旧回执签新版本返回 400 |
| 同一版本只有一份有效签署 | `signatures` 上 `(consent_version_id) WHERE status='VALID'` 部分唯一索引，并发冲突返回 409 |
| 重复签名回执幂等 | `receipt_id` 全局唯一，重复送达返回首次记录（`idempotent_replay=true`），不产生第二份文件 |
| 家属签署须授权 | 家属白名单校验，未授权返回 403 并记录 `SIGN_REJECTED` 告警通知 |
| 患者撤回 | 版本置 `WITHDRAWN`、签名失效、通知手术团队（主刀 + 排期），历史数据与文件全部保留 |
| 文件保存本地 | 版本文件与签署存根写入 `data/files/`，只增不改删；通知同时追加 `notifications.log` |

## 接口概览

- `GET/POST /api/patients`，`POST /api/patients/{id}/authorized-family`
- `GET/POST /api/surgeries`，`GET /api/surgeries/{id}`，`GET /api/surgeries/{id}/readiness`
- `POST /api/surgeries/{id}/consent-versions` — 医生出具/变更版本
- `POST /api/esign/receipts` — 本地模拟签名服务，签发绑定「签署人+内容哈希」的回执
- `POST /api/consent-versions/{id}/sign` — 携带回执签署（幂等）
- `POST /api/consent-versions/{id}/withdraw` — 患者撤回
- `GET /api/notifications` — 通知记录（本地模拟投递）

## 结构

```
app/            FastAPI 后端（models / services / routers / storage / notifier / seed）
static/         Vue 3 前端（本地 vendor，无构建步骤）
tests/          pytest：方案变更、撤回、并发签署、重复回执、家属权限
data/           运行时生成：SQLite 库 + 同意书文件 + notifications.log
run.py          启动入口；start.sh 一键脚本
```

## 验证覆盖（tests/test_flows.py，7 项全过）

1. `test_plan_change_invalidates_old_signature` — 变更后旧签失效、排期提示重签、旧版补签 409、旧回执签新版 400、内容未变拒建版本、仅一份 ACTIVE、历史文件保留
2. `test_withdrawal_flow` — 非患者撤回 403、撤回后版本/签名失效、通知手术团队、文件保留、重复撤回与撤回后签署 409、可重新出具签署
3. `test_concurrent_signing_only_one_valid` — 8 人并发签署同一版本：未授权 403 ×6，授权者中 1×201 + 1×409，最终仅一份 VALID
4. `test_duplicate_receipt_is_idempotent` — 同一回执并发 ×6 + 顺序重放均幂等，仅一条签名记录
5. `test_concurrent_version_creation_keeps_single_active` — 5 并发建版：1×201 + 4×409，仅一份 ACTIVE
6. `test_family_authorization` — 未授权家属 403 + 告警通知、冒用患者 403、缺关系 422、授权家属 201、已签版本拒第二份
7. `test_withdraw_unsigned_version_rejected` — 未签署版本不可撤回
