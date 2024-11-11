import re
from typing import Annotated

import playwright_recaptcha
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.skills.skill_registry import skill
from testzeus_hercules.utils.logger import logger


@skill(
    name="captcha_solver",
    description="solves captcha on the page, should be only used when you are sure that there is a captcha on the page and has to be solved.",
)
async def captcha_solver(
    captcha_type: Annotated[
        str, "type of captcha to solve, ALLOWED VALUES: 'recaptchav2', 'recaptchav3'."
    ],
) -> Annotated[
    bool, "Response if captcha is solved or not. True = solved, False = not solved."
]:
    try:
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
        logger.error(f"An unexpected error occurred during captcha solving: {e}")
        return False
