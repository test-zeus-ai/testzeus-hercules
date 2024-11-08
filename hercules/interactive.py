import asyncio

from hercules.core.runner import CommandPromptRunner

if __name__ == "__main__":
    runner = CommandPromptRunner()
    asyncio.run(runner.start())
