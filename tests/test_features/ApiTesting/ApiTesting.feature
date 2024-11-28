Feature: End-to-End Shopping Flow Validation

  This feature validates the end-to-end shopping flows using all available APIs.

  Background:
    Given the e-commerce API is available at "https://fakestoreapi.com"
    And I have logged in with the username and password via test_data using the login API, you will receive token as response.
    And I have stored the obtained authentication token for future references

  Scenario Outline: User Registration
    When I send a POST request to "/users" with the user details
    Then I should receive a "200 OK" response
    And the response should contain the user's details including a generated "id"
    And I store the user ID as "<userId>"

    Examples:
      | email             | username  | password     | firstname | lastname | city       | street          | number | zipcode | lat       | long       | phone         | userId |
      | user@example.com  | user123   | pass123      | Alice     | Smith    | Metropolis | Main Street     | 101    | 12345   | 40.7128   | -74.0060   | 555-1234      | <generated_id> |

  Scenario Outline: Retrieve All Products
    When I send a GET request to "/products" with query parameters:
      | Parameter | Value        |
      |-----------|--------------|
      | limit     | <limit>      |
      | sort      | <sort_order> |
    Then I should receive a "200 OK" response
    And the response should contain up to "<limit>" products sorted by "id" in "<sort_order>" order
    And each product should include "id", "title", "price", "category", "description", and "image"

    Examples:
      | limit | sort_order |
      | 5     | asc        |

  Scenario Outline: Retrieve Product Categories
    When I send a GET request to "/products/categories"
    Then I should receive a "200 OK" response
    And the response should contain a list of available product categories

  Scenario Outline: Retrieve Products by Category
    Given the category "<category_name>" exists
    When I send a GET request to "/products/category/<category_name>" with query parameters:
      | Parameter | Value |
      |-----------|-------|
      | limit     | <limit> |
    Then I should receive a "200 OK" response
    And the response should contain up to "<limit>" products where "category" is "<category_name>"

    Examples:
      | category_name | limit |
      | electronics   | 3     |

  Scenario Outline: Get a Single Product
    Given a product exists with "id" "<productId>"
    When I send a GET request to "/products/<productId>"
    Then I should receive a "200 OK" response
    And the response should contain the product details with "id" "<productId>"

    Examples:
      | productId |
      | 1         |

  Scenario Outline: Add a Product to Cart
    Given I am authenticated with a valid token
    And a product exists with "id" "<productId>"
    When I send a POST request to "/carts" with the following payload:
      """
      {
        "userId": <userId>,
        "date": "<date>",
        "products": [
          {
            "productId": <productId>,
            "quantity": <quantity>
          }
        ]
      }
      """
    Then I should receive a "200 OK" response
    And the response should contain the created cart with the correct details
    And I store the cart ID as "<cartId>"

    Examples:
      | userId   | date        | productId | quantity | cartId      |
      | 1        | 2023-11-21  | 1         | 2        | <generated> |

  Scenario Outline: Update Cart with Additional Products
    Given a cart exists with "id" "<cartId>" for user "id" "<userId>"
    And I have new product details to add
    When I send a PUT request to "/carts/<cartId>" with the updated cart payload:
      """
      {
        "userId": <userId>,
        "date": "<date>",
        "products": [
          {
            "productId": <productId1>,
            "quantity": <quantity1>
          },
          {
            "productId": <productId2>,
            "quantity": <quantity2>
          }
        ]
      }
      """
    Then I should receive a "200 OK" response
    And the response should contain the cart updated with the new products

    Examples:
      | cartId   | userId   | date        | productId1 | quantity1 | productId2 | quantity2 |
    | 1          | 1        | 2023-11-21  | 1          | 2         | 2          | 1         |

  Scenario Outline: Partially Update Cart Quantity
    Given a cart exists with "id" "<cartId>"
    When I send a PATCH request to "/carts/<cartId>" with the payload:
      """
      {
        "products": [
          {
            "productId": <productId>,
            "quantity": <new_quantity>
          }
        ]
      }
      """
    Then I should receive a "200 OK" response
    And the cart should reflect the updated quantity for "productId" "<productId>"

    Examples:
      | cartId   | productId | new_quantity |
      | 1        | 1         | 4            |

  Scenario Outline: User Checkout Flow
    Given a cart exists for user "id" 1 build a new cart with some random items.
    When I proceed to checkout by deleting the cart at "/carts/<cartId>"
    Then I should receive a "200 OK" response

    Examples:
      | cartId   | userId   |
      | 1        | 1        |

  Scenario Outline: Admin Adds a New Product
    Given I have valid product details:
      | Field       | Value                          |
      |-------------|--------------------------------|
      | title       | <title>                        |
      | price       | <price>                        |
      | category    | <category>                     |
      | description | <description>                  |
      | image       | <image_url>                    |
    When I send a POST request to "/products" with the product details
    Then I should receive a "200 OK" response
    And the response should contain the newly created product with a generated "id"
    And I store the product ID as "<newProductId>"

    Examples:
      | title                | price   | category     | description                         | image_url                          | newProductId |
      | Wireless Headphones  | 199.99  | electronics  | Noise-cancelling wireless headphones | http://example.com/headphones.jpg | <generated>  |

  Scenario Outline: Update a Product's Details
    Given a product exists find products and pick one.
    And I have updated product information:
      """
      {
        "title": "<new_title>",
        "price": <new_price>,
        "description": "<new_description>"
      }
      """
    When I send a PUT request to "/products/<productId>" with the updated details
    Then I should receive a "200 OK" response

    Examples:
      | productId     | new_title              | new_price | new_description                           |
      | <newProductId>| Wireless Headphones Pro| 249.99    | Upgraded noise-cancelling wireless headphones |
