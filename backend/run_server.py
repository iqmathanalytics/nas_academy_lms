import sys
import asyncio
import uvicorn


def main() -> None:
    # On Windows, force selector loop before uvicorn creates the event loop.
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
