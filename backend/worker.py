"""HUB Worker 进程入口。"""
import asyncio
import logging
from hub.database import init_db, close_db
from hub.worker_runtime import WorkerRuntime


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    try:
        runtime = WorkerRuntime()
        # Plan 4 在这里注册具体 handler，例如：
        # from hub.usecases.query_product import handler as query_product_handler
        # runtime.register("query_product", query_product_handler)
        await runtime.run()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
