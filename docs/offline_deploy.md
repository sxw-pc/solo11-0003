# 纯离线环境部署指南

本指南面向完全无外网的目标机器，所有安装介质均在有网环境提前准备，
再通过 U 盘、内网摆渡等方式拷贝过去。全程不访问任何外部地址。

## 一、部署清单

在有网机器上准备以下内容并放入同一个交付目录：

1. Python 3.11 离线安装包（与目标机器架构一致）；
2. FFmpeg 静态二进制或离线安装包；
3. 项目源码目录（含 `requirements.txt`）；
4. Python 依赖 wheel 包目录 `wheels/`。

## 二、Python 3.11 离线安装

### 2.1 Linux

有网机器下载对应发行版的安装包，例如 Debian/Ubuntu 系：

```bash
# 有网机器：下载 deb 及其依赖（不安装）
apt-get download python3.11 python3.11-venv python3.11-distutils
# 或直接从内部软件源获取 tar 包，统一拷贝到目标机器
```

目标机器离线安装：

```bash
sudo dpkg -i python3.11*.deb      # 依赖包按顺序一并安装
python3.11 -m venv .venv
source .venv/bin/activate
python --version                  # 确认输出 Python 3.11.x
```

### 2.2 通用源码方式（无 root 权限时）

```bash
# 有网机器下载 Python-3.11.x.tgz 源码包后拷贝到目标机器
tar -xzf Python-3.11.x.tgz
cd Python-3.11.x
./configure --prefix=$HOME/local/python311
make -j"$(nproc)" && make install
$HOME/local/python311/bin/python3 -m venv .venv
```

## 三、FFmpeg 离线部署

### 3.1 静态二进制（推荐，免依赖）

有网机器从内部软件仓库获取 Linux 静态构建包，解压后得到 `ffmpeg`
与 `ffprobe` 两个可执行文件，拷贝到目标机器固定目录：

```bash
mkdir -p $HOME/local/ffmpeg
cp ffmpeg ffprobe $HOME/local/ffmpeg/
chmod +x $HOME/local/ffmpeg/ffmpeg $HOME/local/ffmpeg/ffprobe
$HOME/local/ffmpeg/ffmpeg -version
```

### 3.2 告诉监帧工坊 FFmpeg 的位置

二选一：

```bash
# 方式一：环境变量（写入 ~/.bashrc 持久化）
export MONITOR_FFMPEG=$HOME/local/ffmpeg/ffmpeg

# 方式二：加入 PATH
export PATH=$HOME/local/ffmpeg:$PATH
```

> 说明：若环境中只有 `ffmpeg` 没有 `ffprobe`，程序会自动回退到
> `ffmpeg -i` 文本解析模式，不影响元数据读取能力。

## 四、Python 依赖 wheel 包离线导入

### 4.1 有网机器下载全部 wheel

```bash
# 建议使用与目标机器相同的 Python 小版本与平台
mkdir wheels
pip download -r requirements.txt -d wheels
```

`wheels/` 目录随项目一起拷贝到目标机器。

### 4.2 目标机器离线安装

```bash
source .venv/bin/activate
pip install --no-index --find-links=wheels -r requirements.txt
```

`--no-index` 保证 pip 完全不访问网络，只从本地目录查找包。

## 五、数据目录与权限配置

```bash
# 项目默认数据目录
mkdir -p data/db data/temp data/output

# SQLite 数据库文件在程序初始化时自动创建，并收紧为 0600 权限
# （仅当前用户可读写）。若使用自定义数据目录：
export MONITOR_DATA_DIR=/srv/monitor-workshop/data
mkdir -p "$MONITOR_DATA_DIR"/{db,temp,output}
chmod 700 "$MONITOR_DATA_DIR"
```

配置项均可通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `MONITOR_DATA_DIR` | 项目内 `data/` | 本地数据根目录 |
| `MONITOR_HOST` | `127.0.0.1` | 监听地址，默认仅本地回环 |
| `MONITOR_PORT` | `8000` | 监听端口 |
| `MONITOR_FFMPEG` | PATH 自动探测 | FFmpeg 可执行文件路径 |
| `MONITOR_KEEP_TEMP` | `0` | 临时文件保留秒数，0 为立即清理 |

## 六、离线验证

```bash
make init-fixture     # 全部素材由本地 FFmpeg 合成，无需网络
make check            # 自检全链路，全部 PASS 即部署成功
make test             # 运行全量离线测试
make run              # 启动服务，本机访问 http://127.0.0.1:8000/docs
```

## 七、注册为系统服务（可选）

Linux 下可创建用户级 systemd 服务（无需 root 的系统级注册）：

```ini
# ~/.config/systemd/user/monitor-workshop.service
[Unit]
Description=Monitor Workshop Local Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/monitor-workshop
Environment=MONITOR_FFMPEG=/home/yourname/local/ffmpeg/ffmpeg
ExecStart=/path/to/monitor-workshop/.venv/bin/python -m app
Restart=on-failure

[Install]
WantedBy=default.target
```

启用命令：

```bash
systemctl --user daemon-reload
systemctl --user enable --now monitor-workshop
systemctl --user status monitor-workshop
```

服务仍只监听 `127.0.0.1`，不会暴露到局域网或公网。

## 八、部署验收对照

- [ ] `python --version` 为 3.11.x
- [ ] `ffmpeg -version` 可正常输出版本信息
- [ ] `pip list` 与 `requirements.txt` 锁定版本一致
- [ ] `make check` 自检全部通过
- [ ] `data/db` 下数据库文件权限为 `-rw-------`
- [ ] 服务监听地址为 `127.0.0.1`，无外部网络连接
