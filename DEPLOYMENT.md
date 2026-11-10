# 部署与公开验收

## GitHub 与 Streamlit Community Cloud

1. 登录 GitHub，创建一个空的 **Public** 仓库，例如 `tradecheck-agent`。在本交付的项目目录执行下列命令，将占位的仓库地址换成实际地址。GitHub 提示认证时，在浏览器授权或使用自己的 GitHub CLI 登录。
2. 登录 Streamlit Community Cloud 并连接 GitHub。选择 **Create app**，填入刚发布的仓库、`main` 分支和入口 `app.py`。在 **Advanced settings** 选择 Python 3.12。 [部署官方文档](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
3. 在 **Secrets** 中填入项目 API Key 和模型名称，保存后部署。把密钥填写在平台界面。评委打开公开页面时无需自己的 Key。 [Secrets 官方文档](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
4. 打开部署面板的分享设置，将应用公开。复制平台实际分配的 HTTPS 地址进行下表验收。 [应用设置官方文档](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings)

```powershell
git init -b main
git add .
git status --short
git diff --cached --check
git commit -m "Publish TradeCheck Agent"
git remote add origin https://github.com/YOUR_ACCOUNT/tradecheck-agent.git
git push -u origin main
```

```toml
DEEPSEEK_API_KEY = "填入服务端 DeepSeek 密钥"
DEEPSEEK_MODEL = "deepseek-flash"
```

将上述设置放在 Secrets 顶层。使用 OpenAI 时，改配 `OPENAI_API_KEY` 和 `TRADECHECK_MODEL`。两种密钥都配置时，应用选用 DeepSeek。

## 验收清单

| 操作 | 通过标准 |
|---|---|
| 无痕浏览器访问公开 HTTPS 地址 | 不登录也能打开首页 |
| 无上传运行案例 A、B、C | 结果分别与 README 中的预期一致 |
| 上传案例 A 的三个不同格式文件 | 解析成功，报告显示原始页码或表格行号 |
| AI 模式运行案例 B | 有真实工具调用日志、四项规则异常和模型解读 |
| 在测试环境临时清空 Key 或使用不可用模型 | 页面显示原因，手动回退规则模式后可正常报告 |
| 修改案例 B 的装箱毛重为 130 kg | 重量异常消失，仍保留三项业务差异和首轮对照 |
| 下载中英文 Excel | 可打开，证据页与结果项一致 |
| 刷新、重新打开与笔记本显示 | 页面可重载，1366×768 下仍可操作 |
| 中国大陆网络访问 | 用当地宽带和手机数据分别打开完整 HTTPS 地址并实测案例 B 和下载 |

交付时已完成本地验证。GitHub 地址、公开 HTTPS 地址、云端真实模型运行及中国大陆访问结果需在发布后登记。

## 中国大陆备用部署方案

可将同一代码放到有公网入口的中国内地云服务器。先根据域名、备案主体与接入商要求办理备案，再使用 HTTPS 反向代理。代理需要转发 Streamlit WebSocket，后端仅监听本机。部署者如有已备案的域名和现有服务器，可使用同一方案。 [阿里云网站备案说明](https://help.aliyun.com/zh/icp-filing/basic-icp-service/product-overview/icp-filing-requirements-for-a-regular-website)

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

通过 systemd 托管进程，入口反代配置可使用如下 Caddyfile，并将域名替换为实际域名。中国大陆备用站可先提供规则模式。若服务器具有可用的模型访问条件，再单独验证 AI 模式。

```caddyfile
tradecheck.example.com {
    reverse_proxy 127.0.0.1:8501
}
```
