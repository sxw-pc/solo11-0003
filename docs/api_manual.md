# 接口使用手册

所有接口仅监听本地回环地址，基础地址：`http://127.0.0.1:8000`。
请求与响应均为 `application/json`（除文件产物外，本服务不提供文件上传）。

## 一、通用错误结构

业务错误统一返回如下结构，HTTP 状态码与错误码配套：

```json
{
  "error": {
    "code": "VIDEO_CORRUPT",
    "message": "视频探测失败，文件可能已损坏"
  }
}
```

### 错误码定义

| 错误码 | HTTP 状态码 | 含义 |
|--------|-------------|------|
| `PATH_INVALID` | 422 | 路径为空、相对路径、含 `..` 或后缀不支持 |
| `VIDEO_EMPTY` | 422 | 文件体积过小，疑似空文件 |
| `VIDEO_CORRUPT` | 422 | 文件损坏、无视频流或无法解析 |
| `VIDEO_RESOLUTION_INVALID` | 422 | 分辨率低于可处理下限 |
| `VIDEO_FPS_INVALID` | 422 | 无有效帧率，疑似裸流损坏 |
| `PARAM_INVALID` | 422 | 请求参数未通过校验 |
| `TASK_NOT_FOUND` | 404 | 任务 ID 不存在 |
| `TASK_STATE_INVALID` | 409 | 任务当前状态不允许该操作 |
| `FFMPEG_NOT_FOUND` | 503 | 本地未检测到可用 FFmpeg |
| `FFMPEG_EXEC_FAILED` | 500 | FFmpeg 子进程执行失败 |
| `DISK_SPACE_INSUFFICIENT` | 507 | 磁盘剩余空间不足 |
| `NO_KEYFRAME` | 400 | 未筛选出任何有效关键帧 |

## 二、任务接口

### 2.1 批量提交处理任务

- **方法/路径**：`POST /api/tasks`
- **请求参数**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `video_paths` | string[] | 是 | - | 本地视频**绝对路径**列表，1-20 个 |
| `mode` | string | 否 | `interval` | `interval` 固定间隔 / `smart` 智能关键帧 |
| `interval_seconds` | int | 否 | 10 | 固定间隔秒数，范围 1-300 |
| `sensitivity` | string | 否 | `medium` | `high`/`medium`/`low` |
| `filter_invalid` | bool | 否 | true | 是否过滤全黑/全白无效帧 |
| `thumb_width` | int | 否 | 320 | 缩略图宽度，范围 160-1920 |
| `orientation` | string | 否 | `vertical` | `vertical`/`horizontal` |
| `keep_originals` | bool | 否 | false | 是否保留关键帧原图 |

- **响应示例**：

```json
{
  "submitted": [
    {"task_id": "9f2c...", "video_path": "/data/cam01.mp4",
     "duration": 3600.0}
  ],
  "rejected": [
    {"path": "/data/bad.mp4", "code": "VIDEO_CORRUPT",
     "message": "视频探测失败，文件可能已损坏"}
  ],
  "queue_depth": 1
}
```

curl 示例：

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"video_paths":["/data/cam01.mp4"],"mode":"smart",
       "sensitivity":"high","keep_originals":true}'
```

Python requests 示例：

```python
import requests

resp = requests.post("http://127.0.0.1:8000/api/tasks", json={
    "video_paths": ["/data/cam01.mp4"],
    "mode": "interval",
    "interval_seconds": 30,
    "orientation": "horizontal",
})
resp.raise_for_status()
task_id = resp.json()["submitted"][0]["task_id"]
```

### 2.2 查询任务状态

- **方法/路径**：`GET /api/tasks/{task_id}`
- **响应关键字段**：`status`（`pending`/`running`/`success`/`failed`/
  `cancelled`）、`keyframe_count`、`montage_path`、`error_code`、
  `error_message`、`keyframes`（关键帧索引数组）。

```bash
curl http://127.0.0.1:8000/api/tasks/9f2c8a1b
```

### 2.3 取消排队任务

- **方法/路径**：`POST /api/tasks/{task_id}/cancel`
- 仅 `pending` 状态可取消；处理中返回 409。

```bash
curl -X POST http://127.0.0.1:8000/api/tasks/9f2c8a1b/cancel
```

## 三、历史记录接口

### 3.1 分页查询记录

- **方法/路径**：`GET /api/records`
- **查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `start_time` | string | 起始时间 ISO8601，可选 |
| `end_time` | string | 结束时间 ISO8601，可选 |
| `status` | string | 按任务状态过滤 |
| `video_name` | string | 视频文件名模糊匹配（不区分大小写） |
| `page` | int | 页码，从 1 开始 |
| `page_size` | int | 每页条数，1-100 |

```bash
curl "http://127.0.0.1:8000/api/records?status=success&page=1&page_size=20"
```

```python
requests.get("http://127.0.0.1:8000/api/records",
             params={"video_name": "cam01", "status": "success"})
```

### 3.2 批量删除记录

- **方法/路径**：`DELETE /api/records`
- **请求体**：

```json
{"ids": ["task-id-1", "task-id-2"], "delete_files": true}
```

- **响应**：

```json
{"deleted": 2, "files_removed": 2, "not_found": []}
```

`delete_files=true` 时同步删除任务对应的本地输出目录与缩略图。

## 四、系统接口

### 4.1 健康检查

`GET /api/system/health` →

```json
{"status": "ok", "ffmpeg_available": true,
 "data_dir": "/workspace/data", "queue_depth": 0}
```

### 4.2 配置查询

`GET /api/system/config`：返回监听地址、数据目录、FFmpeg 路径、
临时保留周期与磁盘剩余空间，不含任何密钥类字段。

### 4.3 一键自检

- **方法/路径**：`POST /api/system/self-check`
- 使用项目自带 fixture 跑通完整流程，结束自动清理临时数据。

```json
{
  "passed": true,
  "checks": [
    {"name": "ffmpeg_available", "passed": true, "detail": "...",
     "message": ""},
    {"name": "pipeline_execution", "passed": true,
     "detail": "status=success, keyframes=4", "message": ""}
  ],
  "summary": "自检 6/6 项通过"
}
```

## 五、任务状态机

```
pending ──出队──▶ running ──成功──▶ success
   │                 └────失败────▶ failed
   └────取消请求────▶ cancelled
```

- 服务重启时，遗留的 `running` 会被重置为 `pending`，与原有排队任务
  一起重新进入串行队列；
- 队列严格串行，后提交的任务在前序任务结束后才开始处理。
