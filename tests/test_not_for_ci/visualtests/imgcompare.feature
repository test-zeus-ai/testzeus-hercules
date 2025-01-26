Feature: Check the appearance of the website with the help of image comparison
#This feature needs a base image for comparison

  Scenario Outline: Compare base image with the website appearance
    Given a user is on the URL as https://testzeus.com
    When the user checks if the application state is similar with the base_image
    Then the visual comparison should be successful
