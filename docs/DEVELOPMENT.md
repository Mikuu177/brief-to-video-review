# 开发与验证

环境：Python3.12、Node22.13+、PostgreSQL15、Redis7、FFmpeg/FFprobe、Poppler、Tesseract含中文、Noto CJK字体和procps的ps。Dockerfile给出可复现的系统依赖安装。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm ci
cd apps/web
npm ci
npm run lint
npm run build
cd ../..
cp .env.example .env
python3 scripts/init_env.py
```

Docker是推荐的完整启动方式。本机开发可自行启动PostgreSQL/Redis后在`.env`设置DATABASE_URL/REDIS_URL，执行`.venv/bin/python scripts/bootstrap.py`，用`PYTHONPATH=apps/api .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8008`运行API，另开终端运行`.venv/bin/python scripts/worker.py`及`.venv/bin/python scripts/recovery_loop.py`。原有`scripts/start_local.sh`是可选macOS/Homebrew便捷脚本，不能代替跨平台安装。

## 无客户数据测试

```bash
docker compose -f compose.test.yaml up -d
.venv/bin/python -m pytest -q
.venv/bin/ruff check apps/api scripts tests
node tests/e2e/workspace_route.cjs
```

测试仅连接单独的`creator_review_test`数据库，并为每例创建、删除随机schema。默认测试端口55433/56380，测试用固定假密码只适用于这个隔离栈。可以通过`TEST_DATABASE_URL`和`TEST_REDIS_URL`覆盖，**不得指向真实数据环境**。

合成视频由测试现场使用FFmpeg生成，不包含客户原片。需要语音的构造夹具可运行`evaluation/fixtures/build_cases.py`或`scripts/generate_content_fixture.py`，遵循脚本的字体/语音输入要求；生成文件写入被忽略的数据目录，不随仓库发布。

修改文档后运行`python3 scripts/update_index.py`，同步文档列表与各文档作用。未经独立标注和真实能力验证，不将测试断言当真实模型准确率。
