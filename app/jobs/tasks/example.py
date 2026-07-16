from saq.types import Context


async def example_task(ctx: Context, *, message: str) -> str:
    return f"processed: {message}"
