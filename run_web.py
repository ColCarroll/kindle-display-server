#!/usr/bin/env python3
"""Entry point for running the web application."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
