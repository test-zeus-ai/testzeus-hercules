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
      
   Scenario Outline: JS dialogues 
    Given a user is on the URL as https://practice.expandtesting.com/js-dialogs
    When the user clicks on the "Js Prompt" button.
    then enters "hello" in the opened dialog box and accept.
    Then the Dialogue Response field on the page should say "hello"
