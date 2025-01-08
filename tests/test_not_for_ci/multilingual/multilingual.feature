Feature: Check multilingual test execution
 Scenario: Verify if the "ホンダ・インテグラ" was sold in 2009
    Given I am on the https://ja.wikipedia.org/
    When I search for "ホンダ・インテグラ"
    Then I should see the search results for "ホンダ・インテグラ"
    And I select the page for "ホンダ・インテグラ" from the search results
    Then I should see information about the "ホンダ・インテグラ" on the page
    And I should find the active sales period of "ホンダ・インテグラ" 
    And then check is should not be sold in 2009