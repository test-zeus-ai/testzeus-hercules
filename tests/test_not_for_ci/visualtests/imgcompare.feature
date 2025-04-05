Feature: Check the appearance of the website with the help of image comparison
#This feature needs a base image for comparison

  Scenario Outline: Compare base image with the website appearance
    Given a user is on the URL as https://testzeus.com
    When the user checks if the application state is similar with the base_image
    Then the visual comparison should be successful


  # This feature displays the image validation capabilities of the agent

  Scenario Outline: Check if the Github button is present in the hero section
    Given a user is on the URL as https://testzeus.com
    And the user waits for 3 seconds for the page to load
    When the user visually looks for a blue colored "Get Access" button
    Then the user should see the button
