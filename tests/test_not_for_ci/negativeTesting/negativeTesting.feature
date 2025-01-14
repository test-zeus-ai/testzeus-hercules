Feature: Negative scenarios for the large action model.
  
 Scenario: Attempting to Log In with Invalid Credentials

  Given I navigate to the GitHub login page at "https://github.com/login"  
  When I enter "invalid_user@example.com" into the "Username or email address" field  
  And I enter "IncorrectPassword123!" into the "Password" field  
  And I click the "Sign in" button  
  Then I should see an error message stating "Incorrect username or password."  
  And I should remain on the login page without being authenticated


 Scenario: Navigating to a Non-existent Page (404 Error)

  Given I am on the Medium website  
  When I navigate to "https://medium.com/non-existent-page"  
  Then I should see an error page with "Out of nothing, something." in a Medium's page


 Scenario: Accessing Restricted Content Without Authentication

  Given I am not logged into Salesforce  
  When I navigate to an account at "https://opensource-demo.orangehrmlive.com/web/index.php/pim/viewPersonalDetails/empNumber/7"  
  Then I should be redirected to the login page  
  And I should see the login page.  


 Scenario: Submitting the Form with an Improperly Formatted Email

  Given I am on the contact form page at "https://www.site24x7.com/tools/email-validator.html"  
  When I enter "Jane Smith" into the email address field
  Then Click validate
  Then I should see an error that explains Please provide a valid email