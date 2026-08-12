# OutlookRegister  

Outlook 注册机  
选择器经常更新，不保证时效性，自行测试。 

- 模拟人类填表操作  
- 自动过验证码  
- 注册成功  

设置相关：  
1.playwright使用性较差,如果使用playwright，则需要自行寻找指纹浏览器并填写绝对路径。  
2.如果使用patchright,且不需要Oauth2，则只需要更改代理地址.  
3.`Bot_protection_wait`单位为秒。  
4.`client_id`与`redirect_url`可以前往[Azure](https://azure.microsoft.com/zh-cn?OCID=cmmyhidqdn5_brandzone__EFID__)注册获取，不需要Oauth2可留空。  
5.`client_id`与`redirect_url`格式通常类似于`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`和`http://localhost:8000`。  
6.`Scopes`按照申请的权限填，不需要Oauth2可留空。  

使用教程：  
1.使用本地代理IP**搭建代理池**，在`config.json`填写你的代理地址。  
2.在设置中调整并发与最大注册量。  
3.如果你需要Oauth2，请在`config.json`中修改`"enable_oauth2"`的值为`true`并填写`Scopes`、`client_id`与`redirect_url`。  
4.安装相关依赖`pip install -r requirements.txt`，如果未安装相关浏览器，使用`patchright install chromium`。  
5.视运行脚本填写或留空`browser_path`。  
6.`python main.py`。  

注意事项：  
**IP**与成功率高度正相关，同一IP短时间不宜多次注册。
邮箱自动存储到工作目录的`Results`下。  

## 域名邮箱辅助验证（新功能）

注册成功后 Outlook 会要求添加辅助邮箱并发送验证码。本工具会自动：

1. 通过域名邮箱服务创建一个随机域名邮箱（如 `tmpxxxxx@tmp7k2x.top`）
2. 把该邮箱填入 Outlook 的辅助邮箱输入框
3. 轮询域名邮箱收件箱，自动提取 Outlook 发来的验证码并回填
4. 把 **outlook 邮箱 ↔ 域名邮箱** 的对应关系写入：

   - `Results/email_mappings.jsonl`（机器可读，面板数据源）
   - `Results/email_mappings.csv`（Excel 可直接打开，每个 Outlook 邮箱一行最新状态）
5. OAuth token 获取成功后，账号按 `email----password----client_id----refresh_token` 四段格式追加到 `Results/accounts.txt`（辅助邮箱验证结束后统一整理；一个账号一行）
6. 历史数据统一整理：`python consolidate_accounts.py` —— 合并 verified_tokens.txt / outlook_token.txt / logged_email.txt 三种旧格式到 `Results/accounts.txt`（按邮箱去重，优先保留带 refresh_token 的记录）

### 代理池（推荐）

单个出口 IP 连续注册会被 Outlook/PerimeterX 记黑名单（症状：验证码 iframe 渲染为空，静默拦截）。
配置代理池后，**每个注册任务随机抽一个代理**：

- `config.json` 加字段：`"proxies": ["http://ip1:port", "http://user:pass@ip2:port"]`
- 或在 `proxies.txt`（与 config.json 同目录，每行一个，`#` 注释）里维护，含账号密码的代理建议放这里（已 .gitignore，不会提交）
- **代理格式三种都认**（自动转换，面板/文件均可直接粘贴）：`http://host:port`、`http://user:pass@host:port`、四段式 `host:port:user:pass`（cliproxy 等卖家给的格式，会自动转成 `http://user:pass@host:port`）
- 代理池为空时回退到 `proxy` 单代理（原行为）

注意：
- 普通 playwright 模式：每个任务随机抽代理建独立上下文（可并发换 IP）
- 指纹浏览器持久化模式（`--user-data-dir`）：代理在浏览器启动时固定，同一 profile 无法按任务换 IP，建议配合 `concurrent_flows` 多开
- 面板统计卡会显示"代理池 N 个"
- **面板直接管理**：打开 http://127.0.0.1:8766 → "代理池管理" 文本框编辑（自动去重）、"保存代理池" 写入 proxies.txt、"测试连通性" 显示每个代理的出口 IP（可测试未保存的内容）

### 配置 `domain_mail_config.json`

复制 `domain_mail_config.example.json` 为 `domain_mail_config.json` 填写（该文件含管理员密钥时不要提交到 git，仓库已 .gitignore）：

```json
{
    "api_base": "https://mail.tmp7k2x.top",
    "domain": "tmp7k2x.top",
    "create_path": "/api/new_address",
    "admin_key": "",
    "enable_recovery_email": true,
    "mail_poll_interval": 5,
    "mail_timeout": 240
}
```

- `create_path` + `admin_key`：默认走公开创建接口；如果服务端是 domain_mail_receiver 仓库那种管理员鉴权部署，改为 `"/admin/new_address"` 并填 `admin_key`
- `enable_recovery_email: false` 可关闭本步骤（恢复原有纯注册行为）
- Outlook 页面改动导致选择器失效时，可在配置加 `"recovery_email_selectors": {...}` 覆盖（键见 `recovery_email.py` 的 `DEFAULT_SELECTORS`）

### 可视化面板（类似 webui）

```powershell
python web_dashboard.py        # 或双击 start_dashboard.bat
```

打开 http://127.0.0.1:8766 ：

- 映射表：Outlook 邮箱 ↔ 域名邮箱 ↔ 验证码 ↔ 状态（自动 3s 刷新，可搜索、复制验证码）
- 实时日志：滚动展示 `Results/register_run.log`（注册流程所有输出）
- 统计卡片 + 一键启动/停止 `main.py` 注册流程
