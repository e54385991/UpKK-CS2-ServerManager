# PostgreSQL 18+ 迁移与运维指南

本项目仅支持 PostgreSQL 18+。SQLModel/SQLAlchemy 仍负责应用数据访问，Alembic 是唯一的数据库结构权威。应用启动时会先验证 PostgreSQL 主版本，再在 session advisory lock 保护下执行 `upgrade head`；升级失败、锁等待超时或数据库版本过低时，应用拒绝启动。

正常部署和应用版本升级不需要编写或手工执行 SQL。开发者修改模型后仍需生成、审查并测试 Alembic Python revision，因为自动比较无法可靠识别字段或表重命名。

## 新部署

Docker Compose 会启动 `postgres:18.6-alpine` 和 Redis：

```bash
cp .env.example .env
docker compose up -d
uv run --no-dev --python 3.14 --locked uvicorn main:app --host 0.0.0.0 --port 8000
```

必须配置：

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=cs2_manager
POSTGRES_PASSWORD=使用独立的高强度密码
POSTGRES_DATABASE=cs2_manager

DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=3600
DB_POOL_PRE_PING=True
DB_ECHO=False
DB_MIGRATION_LOCK_TIMEOUT_SECONDS=300
```

密码由 SQLAlchemy `URL.create()` 编码，包含 `@`、`:`、`/` 等字符时不需要手工 URL 转义。

## 自动 schema 升级

应用生命周期顺序固定为：

1. 验证 PostgreSQL `server_version_num >= 180000`；
2. 获取 PostgreSQL session advisory lock；
3. 执行 Alembic `upgrade head`；
4. 确认数据库 revision 与唯一代码 head 完全一致；
5. 初始化默认数据；
6. 启动后台任务并接受请求。

多个 worker 或多个实例同时启动时，只有持锁连接执行迁移，其他实例等待并在拿到锁后进行幂等检查。等待超过 `DB_MIGRATION_LOCK_TIMEOUT_SECONDS` 会失败关闭，不会用旧 schema 继续运行。

诊断命令：

```bash
# 输出数据库版本、当前 revision、代码 head 和一致性状态
uv run python -m modules.db_admin status

# revision 不一致时返回非零退出码，适用于发布探针
uv run python -m modules.db_admin check

# 受控部署中可提前升级；正常启动不需要调用
uv run python -m modules.db_admin upgrade
```

CI 会禁止多个 Alembic head，并在真实 PostgreSQL 18 上检查空库升级、重复升级、并发升级、模型漂移、JSONB、检查约束、大小写规则和序列。

## 备份与恢复

建议使用 `.pgpass`、容器 secret 或备份平台托管凭据，避免在命令行中暴露密码。

```bash
# 自定义格式备份，适合 pg_restore
pg_dump --host DB_HOST --username cs2_manager --format=custom \
  --file cs2_manager_$(date +%Y%m%d_%H%M%S).dump cs2_manager

# 恢复到已创建的空数据库
pg_restore --host DB_HOST --username cs2_manager --dbname cs2_manager \
  --clean --if-exists cs2_manager_YYYYMMDD_HHMMSS.dump
```

恢复演练必须包含：应用停止写入、恢复备份、运行 `db-admin check`、启动应用和关键业务验证。生产回滚以经过验证的备份恢复为准。

## PostgreSQL 主版本升级

Alembic 只升级应用 schema，不能升级 PostgreSQL 数据库集群。PostgreSQL 18 升到未来主版本时仍需维护窗口，并选择 PostgreSQL 官方支持的 `pg_upgrade`、dump/restore 或逻辑复制方案；先在完整数据副本上验证应用和扩展兼容性，再切换生产流量。

当前首个完整 CI 验证版本是 PostgreSQL 18。未来稳定主版本只有在加入 CI 并通过相同集成合约后才正式声明支持。

## 常见故障

- `PostgreSQL 18+ is required`：连接到了 17 或更低版本，升级集群或修正主机/端口。
- `timed out ... migration lock`：另一实例仍在迁移或持锁连接异常；先检查实例与数据库会话，不要绕过锁。
- `expected exactly one Alembic head`：代码包含分叉 revision；开发者必须合并 head 后重新发布。
- `database heads ... do not match code head`：部署代码与数据库版本不一致；核对镜像、revision 和迁移日志。
