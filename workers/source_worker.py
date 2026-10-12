from client.source_monitor import SourceMonitor

class SourceWorker:
    def __init__(self, telegram):
        self.monitor = SourceMonitor(telegram)
    async def run(self):
        await self.monitor.run()
