# GitHub Token - Quick Reference Card

## 快速参考 / Quick Reference

### Token 格式 / Token Format

✅ **Fine-grained Token (推荐 / Recommended)**
```
[REDACTED]
```
- 以 `github_pat_` 开头 / Starts with `github_pat_`
- 后跟字母、数字和下划线 / Followed by letters, numbers, and underscores
- 长度约 93 个字符 / Length ~93 characters

✅ **Classic Token (经典令牌)**
```
ghp_1234567890abcdefghijklmnopqrstuvwxyz
```
- 以 `ghp_`, `gho_`, `ghu_`, `ghs_`, 或 `ghr_` 开头
- Starts with `ghp_`, `gho_`, `ghu_`, `ghs_`, or `ghr_`

### 如何创建 Token / How to Create Token

1. 访问 / Visit: https://github.com/settings/tokens?type=beta
2. 点击 "Generate new token" → "Fine-grained token"
3. 配置 / Configure:
   - **名称 / Name**: CS2 Server Manager
   - **过期时间 / Expiration**: 90 天 / 90 days
   - **仓库访问 / Repository access**: 选择你的仓库 / Select your repositories
   - **权限 / Permissions**: Contents (只读 / Read-only)
4. 生成并复制 token / Generate and copy token

### 如何使用 / How to Use

1. 登录 CS2 Server Manager / Login to CS2 Server Manager
2. 进入个人中心 / Go to Personal Center
3. 粘贴 token 到 "GitHub Personal Access Token" 字段
4. 输入验证码 / Enter CAPTCHA
5. 点击 "Update Profile" / Click "Update Profile"

### 效果 / Benefits

| 功能 / Feature | 无 Token / Without | 有 Token / With |
|----------------|-------------------|-----------------|
| API 限流 / Rate Limit | 60/小时 / hour | 5000/小时 / hour |
| 私有仓库 / Private Repos | ❌ 不可访问 / No | ✅ 可访问 / Yes |
| 安装成功率 / Success Rate | ⚠️ 低 / Low | ✅ 高 / High |

### 故障排除 / Troubleshooting

#### ❌ Token 格式错误 / Invalid Token Format
**错误 / Error**: "GitHub token must be a valid..."
**解决 / Solution**: 
- 检查 token 是否完整复制 / Check if token is fully copied
- 确认开头是 `github_pat_` 或 `ghp_` 等 / Confirm starts with `github_pat_` or `ghp_`
- 移除多余空格 / Remove extra spaces

#### ❌ Token 已过期 / Token Expired
**错误 / Error**: GitHub API returns 401
**解决 / Solution**:
- 前往 GitHub 重新生成 token / Go to GitHub and regenerate token
- 更新个人中心的 token / Update token in profile

#### ❌ 私有仓库仍无法访问 / Private Repo Still Inaccessible
**检查 / Check**:
1. Token 是否已保存 / Is token saved?
2. Token 权限是否包含该仓库 / Does token have access to that repo?
3. Token 权限是否包含 Contents (Read) / Does token have Contents (Read) permission?

### 安全建议 / Security Tips

✅ **推荐 / Recommended**:
- 使用 Fine-grained tokens (更安全 / more secure)
- 最小权限原则 / Principle of least privilege
- 定期更换 token (90天) / Rotate tokens regularly (90 days)
- 仅授予需要的仓库访问权限 / Only grant access to needed repositories

❌ **不推荐 / Not Recommended**:
- 使用 Classic tokens (权限过大 / too permissive)
- 与他人分享 token / Share tokens with others
- 使用永不过期的 token / Use tokens that never expire
- 授予所有仓库访问权限 / Grant access to all repositories

### 示例 Token 权限配置 / Example Token Permission

```
Token Name: CS2-ServerManager
Expiration: 90 days
Repository access: Only select repositories
  ├─ my-private-plugin-repo ✓
  └─ my-cs2-configs ✓

Permissions:
  ├─ Contents: Read-only ✓
  └─ Metadata: Read-only ✓ (auto)
```

### 常见问题 / FAQ

**Q: Token 会被加密存储吗？**  
**Q: Is the token encrypted in storage?**  
A: Token 作为 API 密钥存储在数据库中。建议使用 Fine-grained token 并限制权限范围。  
A: Token is stored in database as an API key. Use Fine-grained tokens with limited scope.

**Q: 可以使用多个 token 吗？**  
**Q: Can I use multiple tokens?**  
A: 当前每个用户只能配置一个 token。  
A: Currently, each user can only configure one token.

**Q: 不配置 token 可以吗？**  
**Q: Can I use without a token?**  
A: 可以，但只能访问公开仓库，且有较低的 API 限流。  
A: Yes, but you can only access public repos with lower rate limits.

**Q: Token 会在哪些地方使用？**  
**Q: Where is the token used?**  
A: 所有 GitHub API 请求，包括获取 releases、安装插件等。  
A: All GitHub API requests, including fetching releases, installing plugins, etc.

### 帮助 / Support

📖 完整文档 / Full Documentation: `docs/GITHUB_TOKEN.md`  
📋 测试计划 / Test Plan: `docs/TEST_PLAN_GITHUB_TOKEN.md`  
🔄 迁移指南 / Migration Guide: `docs/MIGRATION_GITHUB_TOKEN.md`  
📊 架构图 / Architecture: `docs/GITHUB_TOKEN_FLOW.md`

---

**版本 / Version**: 1.0  
**更新日期 / Last Updated**: 2025-12-07
