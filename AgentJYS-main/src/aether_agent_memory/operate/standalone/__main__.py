"""Convenient module entry point for the independent demo."""

import asyncio

from .demo import main

if __name__ == "__main__":
    asyncio.run(main())
