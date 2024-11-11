Feature: ebikes product website validation
  #This feature tests the ebikes products on the Salesforce platform
      
Scenario: User checks the Dynamo bike
  Given the user is on ebikes home page
  When the user clicks on the Explore More button
  Then the user should be navigated to the Dynamo X2 bike detail page
    
Scenario: User checks the count of bike products
  Given the user is on ebikes home page
  When the user clicks on the Product Explorer button
  Then the user should find 16 products listed on the website and There are two pages of products here.
    
Scenario: User checks the count of bike products for 0 max price
  Given the user is on ebikes home page
  When the user clicks on the Product Explorer button
  And the user slides the Max price to zero
  Then the user should find zero products listed on the website and message as "There are no products matching your current selection"

Scenario: User checks the popup details
  Given the user is on ebikes home page
  When the user clicks on the Product Families button
  And the user clicks on <Product_family_name> product family name
  And the user clicks on the expand button on the right hand of screen
  And the user clicks on RELATED tab
  And the user hovers mouse over <Bike>
  Then the user should be able to see <Product_name> as the pop up message
  |Product_family_name| Bike  |Product_name|
  |     Volt          |VOLT X1|VOLT X1     |
  |     Fuse          |FUSE X1|            |
    