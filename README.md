# 无锡工业气体销售线索生成器

自动从无锡市环评受理公示中提取工业气体用气线索，生成三木化工风格数据型PDF报告，并推送至企业微信。

## 文件结构

```
wuxi-leads/
├── gen_leads.py              # 主脚本：生成 PDF + 发企业微信
├── requirements.txt          # Python 依赖
└── .github/workflows/
    └── daily.yml             # GitHub Actions 每日定时任务
```

## GitHub Actions 配置

### 添加 Secret

在 GitHub 仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**：

| 名称 | 值 |
|------|-----|
| `WXWORK_WEBHOOK_KEY` | 你的企业微信 webhook key（不要提交到代码里）|

### 运行时间

- 自动：每天北京时间 **08:00**（UTC 00:00）
- 手动：仓库 → Actions → 「每日线索报告」→ Run workflow

### 输出

- 控制台日志：PDF 生成状态 + 企业微信推送结果
- Artifacts：PDF 文件（保留7天），可在 Actions 运行记录中下载

## 本地运行

```bash
# 安装字体（Ubuntu/Debian）
sudo apt-get install -y fonts-wqy-zenhei

# 安装依赖
pip install -r requirements.txt

# 运行（需设置环境变量）
WXWORK_WEBHOOK_KEY=你的webhook密钥 OUTPUT_DIR=/tmp python gen_leads.py
```

## 注意事项

- Python 字符串中**禁止使用弯引号** `"` `"` `'` `'`（U+201C/D/8/9），必须用直引号或「」代替，否则会 SyntaxError
- 气体数据均来自环评原文，工期/竣工/建议为推断，不作为合同依据
