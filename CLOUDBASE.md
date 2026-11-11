# 腾讯云 CloudBase 云托管部署

当前版本带账号密码登录，发布前还需在服务端配置三个 `LOGIN_` 环境变量，详见 [登录部署说明](LOGIN_DEPLOYMENT.md)。

当前状态：已准备容器配置，尚未在腾讯云构建或发布；本机未安装 Docker，尚未完成镜像构建验证。

## 先核对额度

在控制台核对资源包/代金券的适用产品、地域和有效期，确认覆盖云托管计算资源及公网流量。云开发额度不能直接推定覆盖所有云托管费用。保持实例运行期间会消耗资源，设置用量告警。

## 控制台部署参数

1. 登录 CloudBase 控制台，选择额度适用的环境，进入云托管，新建**容器型服务**，选择本地代码包上传。代码包根目录应直接包含 Dockerfile、requirements.txt 和 app.py。
2. 服务名建议 `tradecheck`；容器端口 **8501**；Dockerfile 路径 `Dockerfile`，构建目录为项目根目录。
3. 首次体验建议 **1 核 CPU / 2 GB 内存**，根据实际内存和耗时调整。比赛期间设**最小实例数 1、最大实例数 1**，或选择持续运行 1 副本，避免无流量缩容到零。单实例也能保持 Streamlit 当前会话内存状态；发布或重启仍会清空会话，需要重新加载案例。
4. 选择 WEB 公网访问，启用平台提供的 HTTPS 入口。按平台当前要求配置访问权限及域名。自定义域名按平台要求完成备案及证书配置。
5. 在服务端环境变量中配置 `DEEPSEEK_API_KEY` 与 `DEEPSEEK_MODEL=deepseek-flash`。使用 OpenAI 时改配 `OPENAI_API_KEY` 与 `TRADECHECK_MODEL`。不要把密钥提交到 GitHub 或代码包，也不要发到聊天中。无密钥时仍可使用明确标识的规则演示模式。
6. 配置 HTTP 健康检查路径 `/_stcore/health`、端口 8501，启动宽限建议 60 秒。保留 Streamlit 默认 CORS 和 XSRF 防护，不为绕过访问问题关闭它们。
7. 构建发布后将流量切换到新版本，复制**实际分配**的 HTTPS 地址。

## 发布验收

- 用无需登录的浏览器窗口打开首页，检查 WebSocket 连接与页面交互。
- 案例 A 无异常；案例 B 识别四项异常，90×21=1890 不报计算错误。
- 案例 D 完成真实模型表头建议、人工确认、重新审核；API 失败时显示真实错误并可用规则模式。
- 上传 CSV、XLSX、文本 PDF，下载中英文 Excel；确认超时和上传限制足够。
- 页面闲置后重新打开，观察是否仍有 1 个运行实例；分别用大陆宽带和手机网络实测。
- 登记链接、地域、配置、模型调用耗时和测试日期。未完成这些检查前，不应把准备工作描述为云端验收通过。

云托管支持 WebSocket，但实际网关超时、上传限制及域名访问条件仍须在目标环境验证。常驻实例避免缩容到零引起的冷启动，平台维护、进程重启和网络故障仍可能影响访问。

## 官方参考

- [云托管概述与持续运行模式](https://docs.cloudbase.net/run/introduction)
- [控制台及 CLI 部署](https://docs.cloudbase.net/ai/agent-development/deployment/cloud-run)
- [WebSocket 接入](https://docs.cloudbase.net/run/develop/access/websocket)
- [计费相关](https://docs.cloudbase.net/run/faq/fee)
