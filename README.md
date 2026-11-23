# 📡 Trend Radar（早期需求与趋势雷达）

自动化监控 **Hacker News、Reddit、GitHub Trending、Product Hunt** 四大早期信号源的词频异常飙升，经大模型降噪提炼后每日定时推送到 **Lark**（支持签名校验）或 **Telegram**。

## 目录结构

```text
├── .github/workflows/radar.yml   # GitHub Actions 定时任务（每日 08:30 运行）
├── radar.py                      # 核心采集、异常检测、LLM 降噪与 Lark 推送脚本
├── requirements.txt              # Python 依赖
└── README.md
```

## 快速部署步骤

1. 新建一个 GitHub **Private（私有）** 仓库，将本压缩包内的所有文件（注意包含 `.github` 隐藏文件夹）上传至仓库根目录。
2. 进入仓库 **Settings -> Secrets and variables -> Actions**，点击 **New repository secret** 添加密钥：
   - `LLM_API_KEY`（必填）：OpenAI / DeepSeek / OpenRouter 等接口密钥
   - `LLM_BASE_URL`（可选）：默认 `https://api.openai.com/v1`；DeepSeek 填 `https://api.deepseek.com/v1`
   - `LLM_MODEL`（可选）：默认 `gpt-4o-mini`；DeepSeek 填 `deepseek-chat`
   - `LARK_WEBHOOK`（必填）：Lark 群自定义机器人 Webhook URL
   - `LARK_SECRET`（可选）：若 Lark 机器人开启了签名校验，填入对应的 Secret
3. 进入仓库 **Settings -> Actions -> General**，将底部 **Workflow permissions** 设为 **Read and write permissions** 并保存（用于自动保存历史词频数据库 `trends.db`）。
4. 进入仓库 **Actions** 页面，选择 **Daily Trend Radar -> Run workflow** 手动触发测试即可。
