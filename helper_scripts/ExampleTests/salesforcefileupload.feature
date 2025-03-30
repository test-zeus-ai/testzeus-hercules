Feature: User edits contacts with accounts
  Scenario: User edits contacts
    Given I open URL https://testzeus2-dev-ed.lightning.force.com/lightning/r/Account/0015g00001Jp2IaAAJ/view
    When i click on Notes & Attachments. 
    and I upload a file FILE_TO_UPLOAD in Upload Files option inside Notes and Attachment section
    and I confirm the upload by proceeding with "Done"
    Then i should find that file is uploaded properly with screen showing proper metadata of the file.
