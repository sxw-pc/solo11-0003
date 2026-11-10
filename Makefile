# 监帧工坊快捷命令封装
# 自动探测虚拟环境中的 Python（兼容 Linux/macOS 与 Windows），
# 也可通过 make PYTHON=python3 覆盖
ifeq ($(wildcard .venv/bin/python),)
  ifeq ($(wildcard .venv/Scripts/python.exe),)
    PYTHON ?= python3
  else
    PYTHON ?= .venv/Scripts/python.exe
  endif
else
  PYTHON ?= .venv/bin/python
endif

.PHONY: run test check init-fixture cov

## 启动 FastAPI 服务（默认 127.0.0.1:8000）
run:
	$(PYTHON) -m app

## 运行全量单元测试 + 集成测试并输出覆盖率报告
test:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing --cov-report=html --cov-fail-under=85 tests/

## 一键系统自检（FFmpeg、抽帧、拼接、数据库全链路）
check:
	$(PYTHON) -m app --self-check

## 生成离线测试 fixture 素材（全部由本地 FFmpeg 合成）
init-fixture:
	$(PYTHON) tests/fixtures/generate_fixtures.py
