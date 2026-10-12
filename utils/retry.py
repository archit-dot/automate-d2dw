import asyncio
async def retry(fn, attempts=3, delay=2):
    last=None
    for i in range(attempts):
        try: return await fn()
        except Exception as e:
            last=e
            if i+1<attempts: await asyncio.sleep(delay*(i+1))
    raise last
