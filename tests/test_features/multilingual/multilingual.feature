Feature: Check multilingual test execution
  #Home page URL is https://www.unishanoi.org/about
  Scenario: User checks the language change to vietnamese
    Given a user is on the Unishanoi home page
    When the user clicks on the British flag icon with "English" button
    And the user selects "Tiếng Việt" as the locale option
    Then the user should be able to find the header text as "Giới thiệu về UNIS Hà nội" on the page
      
    Kịch bản: Người dùng kiểm tra thay đổi ngôn ngữ sang tiếng Việt
    Giả sử người dùng đang ở trên trang chủ Unishanoi
    Khi người dùng nhấp vào biểu tượng lá cờ Anh với nút "English"
    Và người dùng chọn "Tiếng Việt" làm tùy chọn ngôn ngữ
    Thì người dùng sẽ thấy tiêu đề là "Giới thiệu về UNIS Hà nội" trên trang