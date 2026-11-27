# -*- coding: utf-8 -*-
"""本地启动脚本。"""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    print(f"启动服务：http://{settings.host}:{settings.port}")
    print(f"模型模式：{settings.public_info()['mode']} / {settings.public_info()['model']}")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
