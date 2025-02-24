from typing import Dict, List

from testzeus_hercules.utils.logger import logger


def answer_questions_over_cli(questions: List[str]) -> Dict[str, str]:
    """
    Asks questions over the command line and gets the user's responses.

    Parameters:
    - questions: A list of questions to ask the user, e.g., ["What is your favorite site?", "What do you want to search for?"].

    Returns:
    - A dictionary where each key is a question and each value is the user's response.
    """
    answers: Dict[str, str] = {}
    logger.info("*********************************")
    for question in questions:
        answers[question] = input("Question: " + str(question) + " : ")
    logger.info("*********************************")
    return answers
