# 手动源码与账本维护

源码维护分支 main。只在用户授权时提交、推送和发布，不设置自动备份/代码部署。私钥、真实账号密码、本机私密路径、账单原件、数据库及上传文件不进 Git；实际初始管理员从服务器 root-only 的环境配置注入。

## 手动源码备份

需要后续备份时，检查当前分支、名单与内容后主动执行：

```powershell
Set-Location <project-directory>
git status
git branch --show-current
git add --dry-run .
git add .
git diff --cached
git commit -m "说明本次修改"
git push
```

服务器更新是独立手动操作，推送分支不会自动更新网站，见 [部署说明](deploy/README.md)。

产品需求见仓库内 `REQUIREMENTS.md`。

`.gitignore` 排除 `data/`、`verification/`、`dist/`、依赖缓存、Python 缓存、日志、本机配置、私钥和常见凭据文件。`deploy/known_hosts` 是本机已核对的服务器公钥记录，也不上传。忽略规则不能识别源码内秘密，也不会自动停止跟踪已经提交的文件。

服务器私钥 `<private-key-path>` 留在项目外，仅用作 SSH 身份认证，不打包、不提交、不用于 GitHub 登录。不得把真实密码、token 或私钥内容放进源码、remote URL 或聊天。

## 账本

本地账本位于 `travel-aa/data/`，线上账本位于服务器 `/var/lib/travel-aa/`，彼此独立，源码发布不会复制本地账号到线上。Git 备份不包含账号数据库、上传原文件或用户 Excel。

需要手动备份时，停止相应后台服务，复制整个数据目录到受保护位置，再启动；恢复时也先停止服务，并恢复目录权限。不要将私人数据提交 GitHub。

新设备或新服务器只有源码、没有受保护账本备份时，不会自动恢复账号与消费数据。当前没有设置自动数据备份。
