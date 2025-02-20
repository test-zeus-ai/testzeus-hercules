import asyncio

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.runner import CommandPromptRunner

if __name__ == "__main__":
    conf = get_global_conf()
    conf.set_default_test_id("interactive_runner")
    runner = CommandPromptRunner()
    asyncio.run(runner.start())
