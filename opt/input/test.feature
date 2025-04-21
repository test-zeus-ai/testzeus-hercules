Feature: Interactive Elements on the PfizerForAll Website

Background:
  Given I am on the website "https://www.pfizerforall.com/"

Scenario: Click "Health questionnaires" link
  When I click the "Health questionnaires" link
  Then I should be redirected to the Health Questionnaires page