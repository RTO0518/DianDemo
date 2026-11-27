@echo off
chcp 65001 >nul
title 配电网本体模型与知识推理
cd /d %~dp0

set PY=D:/Conda_env/EduRAG/python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo   配电网本体模型与知识推理
echo   启动后浏览器访问 http://127.0.0.1:8000
echo ============================================
"%PY%" run.py
pause
