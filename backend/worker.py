"""HUB Worker 进程入口。"""
import asyncio
import logging
from functools import partial

from hub.database import close_db, init_db
from hub.handlers.dingtalk_inbound import handle_inbound
from hub.handlers.dingtalk_outbound import handle_outbound
from hub.runtime import bootstrap_dingtalk_clients
from hub.worker_runtime import WorkerRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    # worker 不持 Stream 长连接（避免与 gateway 重复消费），只装 sender + ERP 服务
    clients = await bootstrap_dingtalk_clients(with_stream=False)
    try:
        runtime = WorkerRuntime()

        if (
            clients.dingtalk_sender is not None
            and clients.binding_service is not None
            and clients.identity_service is not None
        ):
            runtime.register("dingtalk_inbound", partial(
                handle_inbound,
                binding_service=clients.binding_service,
                identity_service=clients.identity_service,
                sender=clients.dingtalk_sender,
            ))
            runtime.register("dingtalk_outbound", partial(
                handle_outbound, sender=clients.dingtalk_sender,
            ))
            logger.info("已注册 dingtalk_inbound + dingtalk_outbound handler")
        else:
            logger.warning(
                "钉钉/ERP 配置缺失，未注册 dingtalk_inbound/outbound handler；"
                "去 Web 配置中心补齐后重启 worker"
            )

        await runtime.run()
    finally:
        await clients.aclose()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
