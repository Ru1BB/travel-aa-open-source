# 手动部署指南

本目录提供单服务器、单个同行实例的参考配置。Python 后端在回环地址运行，Nginx 提供 HTTPS；业务数据与发布代码分开。模板中不包含真实服务器地址。

## 参数与模板

- ServerHost：SSH 服务器 IP 或域名。
- PublicHost：浏览器访问的 IPv4 或域名，例如 aa.example.com，不含协议或路径。
- SshUser：具有所需部署权限的 SSH 用户；当前激活脚本要求 root 执行。
- BackendPort：本机服务端口，默认 5182，须与其他项目不同。
- CertificateName：/etc/travel-aa-letsencrypt/live/ 下的证书目录名，默认 travel-aa-ip；域名证书可另命名。
- KeyPath：项目外的私钥文件，或私有环境变量 TRAVEL_AA_SSH_KEY。

```powershell
.\deploy\update.ps1 -ServerHost 'server.example.com' -PublicHost 'aa.example.com' -CertificateName 'aa.example.com' -KeyPath '<private-key-path>'
```

使用前自行核实 SSH 主机指纹，保存到被 Git 忽略的 deploy/known_hosts。不能关闭 StrictHostKeyChecking。发布脚本仅在检查、打包、上传与激活时使用参数，不记录私钥或管理员密码，不自动推送 Git。

nginx.conf、nginx-acme.conf、travel-aa.service 是模板，不能直接复制到正式配置目录：

```sh
python3 deploy/render_config.py /tmp/travel-aa-rendered --public-host aa.example.com --certificate-name aa.example.com --port 5182
```

最终 Host 同时用于 Nginx 和后端允许来源校验。不要只改 Nginx 域名却漏改 systemd 的 --public-host。

## 服务器前置准备

需要 Linux、Python 3.11+、Nginx、systemd、可信 TLS 证书，以及 /srv/travel-aa/releases/ 和对应站点配置。更新脚本面向已安装实例，不是从空服务器安装所有依赖的向导。

证书须位于 /etc/travel-aa-letsencrypt/live/<CertificateName>/fullchain.pem 和 privkey.pem，并配置续期。bootstrap-tls.sh 接收显式 IPv4 参数，仅用于支持短期 IP 证书的 Certbot 初次申请流程；域名证书另行配置。续期工具路径为 /opt/travel-aa-certbot/bin/certbot，应用本身不依赖 Certbot。

## 目录与权限

| 内容 | 约定路径 |
| --- | --- |
| 发布源码 | /srv/travel-aa/releases/<release> |
| 当前源码链接 | /srv/travel-aa/web-current |
| 数据库及上传/导出文件 | /var/lib/travel-aa/ |
| 服务配置 | /etc/systemd/system/travel-aa.service |
| Nginx 配置 | /etc/nginx/sites-available/travel-aa |
| 私有管理员环境配置 | /etc/travel-aa/bootstrap.env |

初始化环境变量 TRAVEL_AA_ADMIN_USERNAME/TRAVEL_AA_ADMIN_PASSWORD 只在空数据库创建账号。环境文件须 root-only 0600，禁止提交 Git；已有账号不被重置。不提供固定默认密码。

持久目录为 travel-aa 系统用户所有、0700，服务 UMask 0077。原文件只能经认证 API 下载，禁止配置成公开静态文件目录。

## 更新、回退与备份

更新脚本白名单打包源码，上传独立发布目录，运行服务器隔离测试，再调用 activate.sh 渲染配置和切换链接。不会删除账本或上传本地数据。回退记录位于 /srv/travel-aa/deploy-state/<UTC时间>/；用 rollback.sh 指定该目录，先确认数据库与旧代码兼容。成员退出机制旧版无法识别，不能盲目回退。

备份与恢复须另外手动操作，见 ../BACKUP.md。代码回退不能恢复被修改的业务数据。不要自动清库或将业务备份推到 GitHub。

## 同机部署其他项目

每个项目使用独立源码目录、数据目录、系统用户、systemd unit、Nginx server 和内部端口。多个项目可以共用公网 80/443，使用 aa.example.com、notes.example.com 等域名由 Nginx 分流，内部端口仅监听 127.0.0.1。新项目不要覆盖本项目的站点或服务。

本目录脚本固定管理 travel-aa 实例；不能直接重复运行它来安装另一个项目或第二个同行实例。
