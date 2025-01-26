Feature: Check the presence of Github button

  # This feature displays the image validation capabilities of the agent

  Scenario Outline: Check if the Github button is present in the hero section
    Given a user is on the URL as https://testzeus.com
    And the user waits for 3 seconds for the page to load
    When the user visually looks for a black colored Github button
    Then the visual validation should be successful
