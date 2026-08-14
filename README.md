# 信用债研究平台部署包

这是可手工上传到 GitHub 的 Flask 部署目录。

## 数据目录

运行态数据（`data/`、`uploads/`、`logs/`、一级定价底稿）外置于独立目录，不随仓库分发。
代码通过环境变量 `PORTAL_DATA_ROOT` 定位数据根目录，未设置时默认使用项目同级 `../juyuan_credit_data`。

- 数据若放在项目同级（`../juyuan_credit_data`），可不设环境变量直接运行。
- 数据放别处时，复制 `.env.example` 为 `.env` 并填入本机路径，或设系统环境变量：
  ```powershell
  $env:PORTAL_DATA_ROOT="D:\信用债研究\完整网页内容\juyuan_credit_data"
  ```
- `config/映射表.xlsx` 随仓库分发；`primary_market_pricing/大智慧财汇元数据表结构 (1).xlsx` 为参考资料，不纳入版本控制，需另行放置。

## 本地运行

```powershell
pip install -r requirements.txt
$env:SITE_PASSWORD="your-password"
$env:ADMIN_PASSWORD="your-admin-password"
$env:SECRET_KEY="change-this-secret"
# 可选：数据不在项目同级时设置
# $env:PORTAL_DATA_ROOT="D:\信用债研究\完整网页内容\juyuan_credit_data"
python app.py
```

访问 `http://127.0.0.1:5000`，先输入 `SITE_PASSWORD`。

## Render/云端建议

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Environment Variables:
- `SITE_PASSWORD`: 全站访问密码
- `ADMIN_PASSWORD`: 后台上传和一键更新的二次验证密码，未设置时默认 `123456`
- `SECRET_KEY`: Flask session 密钥
- `PORTAL_DATA_ROOT`: 数据目录根路径（云端需单独配置持久化数据卷）

## 数据更新

- 择券工具：后台上传 Excel。
- 策略仪表盘：本地运行 Wind 脚本生成 HTML 后上传。
- 利差监控：本地生成 `spread_data.js` 后上传。
- 行业景气度：后台上传静态 HTML。
- 信用债两倍标准差：后台上传页面 HTML 和 `spread_data.js`。
- 机构行为监测：实时代理机构行为上游数据；国债/国开债 1-30 年期曲线由后台“一键更新”的“机构行为曲线”模块增量维护。
- 曲线映射表固定随代码发布，位于 `config/映射表.xlsx`。

## 数据维护

- 后台上传会比较文件内容；内容未变化时不会创建重复备份。
- 内容变化时保留该上传类型最近 3 份历史备份，备份位于 `uploads/`。
- 清理历史冗余前先预览：`python cleanup_redundant_data.py --report logs/data_cleanup_preview.json`。
- 确认清单后执行清理：`python cleanup_redundant_data.py --apply --report logs/data_cleanup_applied.json`。
