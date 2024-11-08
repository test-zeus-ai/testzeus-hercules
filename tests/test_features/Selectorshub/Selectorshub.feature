Feature: Check complex HTML scenarios from Selectorshub

  # This feature tests the browser automation capabilities around Shadow DOM and iframes

  Scenario Outline: Shadow DOM in iframe
    Given a user is on the URL as https://selectorshub.com/shadow-dom-in-iframe/
    When the user clicks on the input box with placeholder "What would you like to have in lunch"
    And the user enters text as "chicken"
    Then the text should be entered successfully in the input box
      
  Scenario Outline: iframe in Shadow DOM
    Given a user is on the URL as https://selectorshub.com/shadow-dom-in-iframe/
    When the user enters UserName as "Hercules"
    And the user clicks on "Close it" button
    Then the "Close it" button should be changed to green color
      
Scenario Outline: Nested iframe
    Given a user is on the URL as https://selectorshub.com/iframe-scenario/
    When the user enters "Hercules" in the Memory Test input box
    And the user clicks on "Lost" button
    Then the text should be persisted on the input box