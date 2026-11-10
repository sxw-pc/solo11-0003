# 监帧工坊

> 纯本地离线的监控录像关键帧提取与摘要拼接工具。所有处理均在本机完成，
> 数据不流出设备，无任何云端依赖。

## 一、核心价值

普通用户本地保存的监控录像存在两大痛点：冗余度高（大量画面静止的片段）、
回看效率低（人工拖动进度条耗时长）。监帧工坊通过传统图像算法自动完成：

- **关键帧提取**：帧间差分 + HSV 直方图比对，自动筛掉静止与无效画面；
- **摘要长图拼接**：把关键帧统一缩略并叠加时间戳，一张长图纵览全程；
- **本地任务管理**：轻量串行队列 + SQLite 持久化，服务重启自动恢复；
- **隐私优先**：不读取摄像头、不接入实时流、不发起任何外部网络请求。

## 二、功能清单（F1-F8）

| 编号 | 功能 | 说明 |
|------|------|------|
| F1 | 本地视频接入与校验 | 支持 mp4/avi/mov，校验存在性、格式、完整性并读取元数据 |
| F2 | 抽帧规则自定义 | 固定间隔（1-300 秒）或智能关键帧（高/中/低灵敏度），可过滤全黑全白帧 |
| F3 | 纯本地关键帧检测 | 帧差法 + HSV 直方图，无云端 AI 调用，自动跳过片头静态片段 |
| F4 | 缩略图摘要拼接 | 统一缩略图（宽 160-1920）、横/纵向拼接、左上角时间戳叠加 |
| F5 | 本地异步任务调度 | 单线程串行队列，状态查询、失败错误码、排队取消、重启恢复 |
| F6 | 处理记录持久化 | SQLite 存储任务全量元数据，多条件查询、批量删除、可选删文件 |
| F7 | 离线环境自检 | 一键用自带素材跑通全链路，输出标准化自检报告并自动清理 |
| F8 | 临时资源自动回收 | 任务结束清理临时帧，启动清理异常退出残留，保留周期可配 |

## 三、硬件与系统要求

- 操作系统：Linux / macOS / Windows（需可运行本地 FFmpeg）
- Python：3.11
- FFmpeg：6.0 及以上（开发环境也兼容 4.2 静态二进制）
- 磁盘：建议预留待处理视频体积 2 倍以上的可用空间
- 内存：512MB 以上即可，4K 视频建议 1GB 以上
- 网络：**运行与测试全程不需要网络**

## 四、快速上手

### 4.1 在线环境准备依赖

```bash
# 1. 创建并激活虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate        # Windows 执行 .venv\Scripts\activate

# 2. 安装锁定版本依赖
pip install -r requirements.txt

# 3. 确认本地 FFmpeg（如不在 PATH，可用环境变量指定）
ffmpeg -version
export MONITOR_FFMPEG=/usr/local/bin/ffmpeg
# Windows PowerShell 对应写法：
# $env:MONITOR_FFMPEG = "C:\本地路径\ffmpeg.exe"
```

### 4.2 离线环境准备依赖

纯断网环境的完整部署步骤见 [docs/offline_deploy.md](docs/offline_deploy.md)。
简要流程：在有网机器执行 `pip download -r requirements.txt -d wheels` 下载
wheel 包，随项目拷贝到目标机器后 `pip install --no-index --find-links=wheels -r requirements.txt`。

### 4.3 生成测试素材并自检

```bash
make init-fixture     # 用本地 FFmpeg 合成全部模拟监控视频
make check            # 一键自检：FFmpeg、抽帧、拼接、数据库全链路
```

### 4.4 启动服务与运行测试

```bash
make run              # 启动 FastAPI，默认监听 127.0.0.1:8000
make test             # 全量单元 + 集成测试并输出覆盖率报告
```

启动后可在本机浏览器打开 OpenAPI 文档：

- 接口文档（Swagger UI）：`http://127.0.0.1:8000/docs`
- OpenAPI 描述：`http://127.0.0.1:8000/openapi.json`

### 4.5 第一个任务（本地 curl 示例）

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "video_paths": ["/绝对路径/tests/fixtures/mock_moving.mp4"],
    "mode": "smart",
    "sensitivity": "medium",
    "thumb_width": 320,
    "orientation": "vertical"
  }'

# 用返回的 task_id 查询状态
curl http://127.0.0.1:8000/api/tasks/<task_id>
```

## 五、快捷命令说明

| 命令 | 作用 |
|------|------|
| `make run` | 启动 FastAPI 服务（默认 127.0.0.1:8000） |
| `make test` | 运行全量测试并生成覆盖率报告（阈值 85%） |
| `make check` | 执行离线环境自检 |
| `make init-fixture` | 通过本地 FFmpeg 生成测试 fixture 素材 |

也可直接用模块方式运行：

```bash
python -m app                  # 启动服务
python -m app --self-check     # 命令行自检
```

## 六、常见问题排查

| 现象 | 排查方向 |
|------|----------|
| 自检报 `FFMPEG_NOT_FOUND` | 确认 FFmpeg 已安装，或设置 `MONITOR_FFMPEG` 指向可执行文件 |
| 提交返回 `PATH_INVALID` | 仅接受**绝对路径**，且不能包含 `..`，后缀仅限 mp4/avi/mov |
| 提交返回 `VIDEO_CORRUPT` | 文件损坏或无视频流，可先用 `ffmpeg -i 文件` 自行确认 |
| 任务失败 `DISK_SPACE_INSUFFICIENT` | 清理输出目录所在磁盘空间后重新提交 |
| 智能模式关键帧过少 | 切换灵敏度 `high`，或关闭 `filter_invalid` 观察是否为全黑画面 |
| 服务重启后任务消失 | 检查 `data/db` 目录权限，任务状态持久化在 SQLite 中 |
| 端口 8000 被占用 | 设置环境变量 `MONITOR_PORT=8001` 或启动时加 `--port` |

## 七、目录结构详解

```
监帧工坊/
├── app/
│   ├── core/              # FFmpeg 封装、视频校验、关键帧算法、拼接、任务队列
│   ├── api/               # FastAPI 路由与 Pydantic 数据模型
│   ├── db/                # sqlite3 连接管理与表结构
│   ├── utils/             # 配置、文件、时间工具
│   └── __main__.py        # 应用工厂与服务入口
├── tests/
│   ├── fixtures/          # 本地 FFmpeg 合成的离线素材与生成脚本
│   ├── unit/              # 单元测试
│   └── integration/       # 集成测试
├── docs/                  # 离线部署、接口手册、算法说明
├── data/                  # 本地运行数据（db/temp/output，已 gitignore）
├── requirements.txt       # 依赖精确版本锁定
├── Makefile               # 快捷命令
└── pytest.ini             # 测试配置
```

## 八、能力边界

本工具**不提供**：实时流（RTSP/RTMP）接入、人脸识别等 AI 能力、Web 前端、
分布式部署、视频剪辑转码、账号权限、远程上传、独立 CLI 交互。所有输入输出
均为本地磁盘路径，面向单本地用户使用。
