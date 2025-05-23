import re
import traceback
from typing import Annotated

import playwright_recaptcha
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.automation.add_item import add_method

@tool(
    agent_names=["browser_nav_agent"],
    name="captcha_solver",
    description="solves captcha on the page, should be only used when you are sure that there is a captcha on the page and has to be solved.",
)
async def captcha_solver(
    captcha_type: Annotated[str, "Captcha type: recaptchav2/recaptchav3"],
) -> Annotated[bool, "Solve status: True=solved, False=failed"]:
    try:
        print('__-------____-----____-----__---')
        print("Tool used captcha_solver.")
        add_method("captcha_solver", str([captcha_type]))
    
        browser_manager = PlaywrightManager()
        page = await browser_manager.get_current_page()
        captcha_solver = getattr(playwright_recaptcha, captcha_type)
        async with captcha_solver.AsyncSolver(page) as solver:
            page = await browser_manager.get_current_page()
            token = await solver.solve_recaptcha()
            logger.info(token)
        score_pattern = re.compile(r"Your score is: (\d\.\d)")
        score_locator = page.get_by_text(score_pattern)
        logger.info(await score_locator.inner_text())
        return True
    except Exception as e:

        traceback.print_exc()
        logger.error(f"An unexpected error occurred during captcha solving: {e}")
        return False
