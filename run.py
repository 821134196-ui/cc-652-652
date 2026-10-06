#!/usr/bin/env python3
"""一条命令启动整个系统：python run.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")
