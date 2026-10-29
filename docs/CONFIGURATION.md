# 配置指南

复制`.env.example`后运行`python3 scripts/init_env.py`。数据库密码在本机随机生成；真实`.env`被Git和Docker构建上下文排除。默认`AUTH_MODE=local`，无需登录，不生成默认账户密码。

| 字段 | 用途 |
| --- | --- |
| ARK_API_KEY | 火山方舟API Key，保存在服务端 |
| ARK_BASE_URL | 方舟API地址 |
| ARK_TEXT_MODEL | 开通的文本模型ID或端点，用于Brief与脚本 |
| ARK_VIDEO_MODEL | 支持原生视频输入的模型ID或端点 |
| ARK_PREPROCESS_MODEL | 原生视频文件处理所需模型 |
| ASR_API_KEY / ASR_RESOURCE_ID | 火山语音录音文件识别接口及相应资源ID |
| TOS_ACCESS_KEY / TOS_SECRET_KEY | 专用存储凭据，仅授予本工具所需对象前缀权限 |
| TOS_BUCKET / TOS_ENDPOINT / TOS_REGION | 私有桶及所在区域，供短时签名音频URL使用 |
| SOURCE_INTEGRITY_ENABLED | 开启来源逐页/区域读取 |
| VIDEO_CONTENT_ENABLED | 开启独立视频内容读取 |
| REVIEW_BIND_PORT | Docker对本机开放的端口，默认8008 |

模型名称和服务资源以你实际开通的能力为准。本项目实现了方舟文件上传/状态查询和语音协议，不能认为任意OpenAI风格接口仅改模型名就能处理视频。

原件默认保存在本项目私有卷；ASR通过短时签名URL读取音频。不要把桶设为公开读，也不要把音频签名URL当成永久链接发布。

`ARK_VIDEO_AUDIO_VERIFIED=false`与`REVIEW_LIVE_EVALUATION_APPROVED=false`默认关闭。前者只能在确认模型真实读取原片音轨后改变；后者需要冻结评测标准并通过独立标注验证。测试时使用界面显式提供的技术试审路径，保留限制；不要为获得绿色结果直接改门槛。

可选`AUTH_MODE=session`沿用账户/会话隔离，需要设置随机`SESSION_SECRET`及自己的`ADMIN_PASSWORD`，执行初始化后使用登录页面。默认本机模式不需要它。切勿把两种模式切到同一套已有客户数据库上混用。
