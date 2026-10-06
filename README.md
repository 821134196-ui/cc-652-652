# 手术知情同意版本与撤回系统

面向医院手术排期场景的知情同意管理系统：医生按方案生成同意书版本（术式 / 麻醉方式 / 风险），
患者或**获授权家属**签署；手术方案关键内容变更后，旧签署立即不再满足手术准备条件，排期页明确
提示重新签字；患者可撤回同意，原文件失效并通知手术团队，历史文件始终保留。

- 后端：Python + FastAPI + SQLAlchemy + SQLite
- 前端：Vue 3 + Vite
- 签名接口、团队通知：本地模拟（回执落日志、签署文件落本地磁盘）

## 一条命令启动

```bash
./run.sh            # 自动安装依赖、初始化 SQLite 与演示数据、同时启动前后端
./run.sh --reset    # 清空本地数据后重新初始化
```

启动后：

| 服务 | 地址 |
| --- | --- |
| 前端（手术排期页） | http://127.0.0.1:5173 |
| 后端 API | http://127.0.0.1:8000 |
| 接口文档（Swagger） | http://127.0.0.1:8000/docs |

停止：终端按 `Ctrl+C`，前后端进程会一起退出。

运行时数据全部在 `backend/data/`（已 gitignore）：

- `consent.db` —— SQLite 数据库（版本、签署、授权、通知）
- `consents/surgery_<id>/v<n>_signed_<回执号>.txt` —— 本地保存的签署文件
- `sign_gateway.log` / `notify_gateway.log` —— 模拟签名网关 / 团队通知推送记录

## 演示数据

首次启动自动写入 4 台手术，覆盖排期页全部状态：

| 患者 | 手术 | 状态 |
| --- | --- | --- |
| 王秀兰 | 腹腔镜 → 开腹胆囊切除术（术式/麻醉变更） | ⚠ 方案变更 · 须重新签字 |
| 李国强 | 腹股沟疝修补术（已签，含一名**未授权**家属"李敏"） | ✓ 可排期 |
| 赵敏 | 甲状腺次全切除术（已生成、未签署） | ◔ 待签署 |
| 陈建华 | 膝关节镜手术（已签署后撤回） | ✕ 已撤回 · 禁止排期 |

## 关键业务规则与实现位置

| 规则 | 实现 |
| --- | --- |
| 同一手术至多一份当前有效版本 | 部分唯一索引 `uq_active_version`（仅对 `status='ACTIVE'` 生效），新建版本与旧版失效在同一事务提交，见 `app/services.py::create_version` |
| 关键内容变更 → 旧签名失效、须重签、禁止复制旧签名 | 旧版置 `SUPERSEDED` 且旧签名 `invalidated_at` 落戳；旧回执对新版无效（按回执只查到旧记录，不会为新版生成文件）；排期判定 `assess_readiness` 返回 `RESIGN_REQUIRED` |
| 并发签字 / 重复回执不得产生两份有效文件 | `signatures(version_id)` 唯一索引兜底；竞争失败者捕获 `IntegrityError` 后返回获胜签名，`created=false`，不写第二份文件；`gateway_receipt_id` 与 `idempotency_key` 也各有唯一约束（幂等重放） |
| 未授权家属不能有效签署（或撤回） | `_resolve_signer` 校验家属归属与 `authorized` 标记，未授权返回 403 `UNAUTHORIZED_FAMILY`，且发生在发放回执 / 写文件之前 |
| 撤回 → 通知团队 + 文件失效 + 历史留存 | 版本置 `WITHDRAWN`、签名失效、写通知并调用模拟推送；磁盘文件不删除，只追加撤回记录与"已失效（历史留存）" |
| 排期不能凭旧同意书 | 排期看板只认 `assess_readiness=READY`（当前 ACTIVE 版本且其签名未失效） |

## 接口一览

```
POST   /api/patients                              新建患者
GET    /api/patients                              患者列表
POST   /api/patients/{id}/family                  登记家属（authorized 表示是否已授权）
PATCH  /api/family/{id}/authorization             授权 / 撤销家属
POST   /api/surgeries                             登记手术
GET    /api/surgeries                             排期看板（含每台手术 readiness 判定）
GET    /api/surgeries/{id}                        手术详情（版本时间线/家属/通知）
POST   /api/surgeries/{id}/versions               医生生成新版（旧版自动失效并通知重签）
POST   /api/surgeries/{id}/sign                   患者/授权家属签署（支持 idempotency_key / gateway_receipt_id）
POST   /api/surgeries/{id}/withdraw               患者/授权家属撤回
GET    /api/notifications? surgery_id=...         通知列表
```

签署请求体示例：

```json
{ "signer_type": "FAMILY", "family_id": 1, "idempotency_key": "client-uuid-xxx" }
```

## 验证完整流程

```bash
cd backend
python3 -m pytest tests/ -v
```

8 个测试全部通过（含 3 个基于真实 uvicorn + 线程池 HTTP 并发的用例）：

- `tests/test_plan_change.py` —— **方案变更**：v1 签署 → 术式/麻醉变更生成 v2 → v1 与旧签名
  失效、排期提示"须重新签署"、旧回执无法用于新版、v2 重签后恢复 READY、新旧两份文件均在
- `tests/test_withdraw.py` —— **撤回**：撤回后状态 `WITHDRAWN`、禁止排期与再签署、团队收到通知、
  文件保留并追加撤回记录、医生再出新版后可重新签署
- `tests/test_concurrency.py` —— **并发签署**：患者与家属 6 个并发请求（含重复幂等键）、
  同一签名回执并发重投 4 次、变更后 5 个并发签署 —— 每种场景均只产生 1 条签名、1 份文件，
  只有 1 个请求 `created=true`
- `tests/test_family_auth.py` —— **家属权限**：未授权家属签署/撤回返回 403 且无任何文件产生、
  跨患者家属 404、授权撤销后对新版签署被拒、患者本人签署不受影响

## 前端页面

- **手术排期看板**（`/`）：按状态筛选；每行醒目徽标 + 系统判定文案，只有绿色"已签署·可排期"
  的手术满足准备条件，变更/撤回场景直接显示阻断原因。
- **手术详情**（`#/surgeries/:id`）：版本时间线（当前/已替代/已撤回）、每版术式麻醉风险与
  签署状态（失效签名明确标注"不得复制到新版"）、医生生成新版弹窗（提交前二次警示旧签失效）、
  患者/家属签署、授权家属撤回、家属登记与授权管理、本手术通知流。
- **通知中心**（`#/notifications`）：签署完成 / 须重签 / 撤回三类团队通知。

## 目录结构

```
run.sh                     一键启动
backend/
  app/
    main.py                FastAPI 路由
    models.py              ORM 模型与数据库级唯一约束
    schemas.py             Pydantic 模型
    services.py            版本更迭 / 签署 / 撤回 / 排期判定（全部事务化）
    documents.py           签署文件渲染与撤回标记（本地存储）
    gateways.py            签名网关 / 通知推送的本地模拟
    database.py            SQLite 引擎（WAL + busy_timeout）
  seed.py                  演示数据
  tests/                   端到端验证（含真实 HTTP 并发）
frontend/
  src/views/               排期看板 / 手术详情 / 通知中心
  src/components/          状态徽标等
```
