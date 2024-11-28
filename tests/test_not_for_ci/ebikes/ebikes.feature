Feature: ebikes product website validation
  #This feature tests the ebikes products on the Salesforce platform
      
Scenario: User checks the Dynamo bike
  Given the user is on ebikes home page
  When the user clicks on the Explore More button, wait for the next screen to load
  Then the user should be navigated to the Dynamo X2 bike detail page, with absolute match
    
Scenario: User checks the count of bike products
  Given the user is on ebikes home page
  When the user clicks on the Product Explorer button
  Then the user should find 16 products listed on the website by a paginator value and by counting there should be 9 on the 1st page and There are two pages of products here.
    
Scenario: User checks the count of bike products for 0 max price
  Given the user is on ebikes home page
  When the user clicks on the Product Explorer button
  And the user slides the Max price to zero
  Then the user should find zero products listed on the website and message as "There are no products matching your current selection"

Scenario: User checks the popup details
  Given the user is on ebikes home page
  When the user clicks on the Product Families button
  And for each Product_family_name from the test data, click on the right Product_family_name
    then user clicks on RELATED tab
    then user hovers over the <Bike> name from the current row of test data
    Then the user should be able to see a popup with message as <Product_name> the current row of test data, check this twice
    Then clicks on the Product Families button and move to the next record in the test data
    