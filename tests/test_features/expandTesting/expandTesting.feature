Feature: Check complex HTML scenarios from ExpandTesting website

  Scenario Outline: jQuery UI menu
    Given a user is on the URL as https://practice.expandtesting.com/jqueryui/menu#
    When the user hovers on the "Enabled" menu
    And the user clicks on "Back to jQuery UI link"
    Then the user should be navigated to "https://practice.expandtesting.com/jqueryui/menu#" page
      
    
  Scenario Outline: Tooltips check
    Given a user is on the URL as https://practice.expandtesting.com/tooltips
    When the user hovers on the "Tootlip on bottom" button
    Then the user should be able to see "Tooltip on bottom" tooltip in orange box at the bottom of the button
      
    
   Scenario Outline: Redirection testing
    Given a user is on the URL as https://practice.expandtesting.com/redirector
    When the user clicks on the "here" link
    Then the user should be able to navigated to URL as "https://practice.expandtesting.com/status-codes"
      
    
   Scenario Outline: JS dialogues 
    Given a user is on the URL as https://practice.expandtesting.com/js-dialogs
    When the user clicks on the "Js Prompt" button
    And the user enters "hello" in the opened javascript dialogue
    And the user clicks "Ok" button
    Then the user should be able to see "hello" in the Dialogue Response field on the page
      
    
   Scenario Outline: New window check
    Given a user is on the URL as https://practice.expandtesting.com/windows
    When the user clicks on the "Click Here" link
    Then the user should be navigated to a new window with the text as "Example of a new window page for Automation Testing Practice"
      
   Scenario Outline: Captcha checks
    Given a user is on the URL as https://developer.servicenow.com/app.do#!/home
    When the user enters "Hercules" as the first name
    And the user enters "Son of Zeus" as the last name
    And the user enters "123@gmail.com" as the email
    And the user enters "India" as the country
    And the user enters "234" as the password
    And the user enters "234" as the confirm password
    And the user clicks on reCAPTCHA checkbox
    And the user clicks on the checkbox for terms and conditions
    And the user clicks on "Sign up" button
    Then the user should be able to see "Thank You" message
      
      
    