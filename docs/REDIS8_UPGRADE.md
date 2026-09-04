# Redis 8 升级与回滚说明

默认 Compose 镜像已更新为固定摘要的 `redis:8.10.1-alpine`。应用只使用 Redis 7/8 通用命令和数据
编码，因此外部 Redis 7 部署仍可继续工作。升级不修改 key 名称、TTL 或序列化格式。

## 升级前检查

- 确认 `redis-cli INFO persistence` 中没有正在进行的 RDB/AOF 重写；
- 备份 `/data` 卷或导出关键业务 key；
- 在预发布环境运行 `docker compose config`、健康检查和完整基线；
- 确认应用启动日志显示 Alembic 已完成数据库升级后再接收流量。

## Compose 升级

```bash
docker compose pull redis
docker compose up -d redis
docker compose ps
docker compose logs --tail=100 redis
```

健康检查通过后重启应用，使共享 Redis client 重新建立连接。应用会继续使用部署进度、
批量动作和协调锁的原有 key。

## 回滚

若出现连接错误或延迟异常，先保留数据卷，再将 Compose 镜像改回经过验证的 Redis 7
镜像并重新创建容器：

```bash
docker compose up -d --no-deps redis
```

不要删除 `redis_data` 卷。回滚后运行应用健康检查和 `uv run python scripts/check_baseline.py`；
只有确认读写和 TTL 正常后才恢复流量。Redis 8 已写入的数据使用兼容协议，通常可由 Redis 7
继续读取；若厂商镜像行为不兼容，应从升级前备份恢复到新的临时卷后再切换。
