Feature: Check complex HTML scenarios from ExpandTesting website
  # in any case if you get stuck repeat the steps after refreshing the page.
  # if anytime https://practice.expandtesting.com/windows#google_vignette is the base url then refresh and start the process again.
  # close all the google popups on the screen if any surface

  Scenario Outline: jQuery UI menu
    Given a user is on the URL as https://practice.expandtesting.com/jqueryui/menu#
    When user scroll down to the bottom.
    When the user hover on enabled menu.
    then a new menu opens.
    When user scroll down to the bottom.
    And the user clicks on "Back to jQuery UI link" option, if nothing happens then click again
    Then the user should be navigated to "https://practice.expandtesting.com/jqueryui" page
      
    
  Scenario Outline: Tooltips check
    Given a user is on the URL as https://practice.expandtesting.com/tooltips
    When the user hovers on the "Tootlip on bottom" button
    Then the user should be able to see "Tooltip on bottom" tooltip in box at the bottom of the button
      
    
   Scenario Outline: Redirection testing
    Given a user is on the URL as https://practice.expandtesting.com/redirector
    When the user clicks on the "here" link, if nothing happens then click again
    Then the user should be able to navigated to URL as "https://practice.expandtesting.com/status-codes"
      
    
   Scenario Outline: JS dialogues 
    Given a user is on the URL as https://practice.expandtesting.com/js-dialogs
    When the user clicks on the "Js Prompt" button.
    then enter text "hello" in the prompt of opened dialog box and accept.
    then user scroll down to the bottom.
    Then the Dialogue Response field on the page should say "hello", if not found then try again.
      
    
   Scenario Outline: New window check
    Given a user is on the URL as https://practice.expandtesting.com/windows
    When the user clicks on the "Click Here" link, if nothing happens then click again
    Then the user should be navigated to a new window with the text as "Example of a new window page for Automation Testing Practice"
    