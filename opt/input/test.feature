Feature: Verification of Elements on https://www.pfizerforall.com/

  Background:
    Given the user navigates to "https://www.pfizerforall.com/"


  @heading @visibility
  Scenario: Verify visibility of "Get Help With"
    Then the user should see the heading "Get Help With"

  @heading @visibility
  Scenario: Verify visibility of "Vaccines"
    Then the user should see the heading "Vaccines"

  @heading @visibility
  Scenario: Verify visibility of "Migraine"
    Then the user should see the heading "Migraine"

  @heading @visibility
  Scenario: Verify visibility of "COVID-19 & Flu"
    Then the user should see the heading "COVID-19 & Flu"

  @heading @visibility
  Scenario: Verify visibility of "Additional Resources"
    Then the user should see the heading "Additional Resources"

  @heading @visibility
  Scenario: Verify visibility of "Navigatemenopause"
    Then the user should see the heading "Navigatemenopause"

  @heading @visibility
  Scenario: Verify visibility of "Stay on top of your health.A few simple questions can keep you informed and in control."
    Then the user should see the heading "Stay on top of your health.A few simple questions can keep you informed and in control."

  @heading @visibility
  Scenario: Verify visibility of "Cancer"
    Then the user should see the heading "Cancer"

  @heading @visibility
  Scenario: Verify visibility of "Heart health"
    Then the user should see the heading "Heart health"

  @heading @visibility
  Scenario: Verify visibility of "Migraine"
    Then the user should see the heading "Migraine"

  @heading @visibility
  Scenario: Verify visibility of "Vaccines"
    Then the user should see the heading "Vaccines"

  @heading @visibility
  Scenario: Verify visibility of "COVID-19, flu, or just a cold? Let’s find out."
    Then the user should see the heading "COVID-19, flu, or just a cold? Let’s find out."

  @heading @visibility
  Scenario: Verify visibility of "A newer way to manage migraine is here."
    Then the user should see the heading "A newer way to manage migraine is here."

  @heading @visibility
  Scenario: Verify visibility of "Answers to your health & wellness questions"
    Then the user should see the heading "Answers to your health & wellness questions"

  @heading @visibility
  Scenario: Verify visibility of "Find and schedule vaccines you and your family may be eligible for."
    Then the user should see the heading "Find and schedule vaccines you and your family may be eligible for."

  @heading @visibility
  Scenario: Verify visibility of "We’re committed to helping you afford your Pfizer medications."
    Then the user should see the heading "We’re committed to helping you afford your Pfizer medications."

  @heading @visibility
  Scenario: Verify visibility of "Sign up to stay informed with news and updates"
    Then the user should see the heading "Sign up to stay informed with news and updates"

  @paragraph @visibility @content
  Scenario: Verify visibility of the "Get Help With" paragraph
    Then the user should see a paragraph starting with "Get Help With"

  @paragraph @visibility @content
  Scenario: Verify visibility of the "COVID-19 & Flu" paragraph
    Then the user should see a paragraph starting with "COVID-19 & Flu"

  @paragraph @visibility @content
  Scenario: Verify visibility of the "Additional Resources" paragraph
    Then the user should see a paragraph starting with "Additional Resources"

  @paragraph @visibility @content
  Scenario: Verify visibility of the "Take health questionnaires" paragraph
    Then the user should see a paragraph starting with "Take health questionnaires"

  @paragraph @visibility @content
  Scenario: Verify visibility of the "Manage migraine" paragraph
    Then the user should see a paragraph starting with "Managemigraine"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Skip to Content' link
    Then the user should see the link with text "Skip to Content"
    When the user clicks the "Skip to Content" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/#main-content-block"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Home' link
    Then the user should see the link with text "Home"
    When the user clicks the "Home" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Health questionnaires' link
    Then the user should see the link with text "Health questionnaires"
    When the user clicks the "Health questionnaires" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/healthquestionnaires"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Migraine' link
    Then the user should see the link with text "Migraine"
    When the user clicks the "Migraine" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Menopause' link
    Then the user should see the link with text "Menopause"
    When the user clicks the "Menopause" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/menopause"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Vaccines' link
    Then the user should see the link with text "Vaccines"
    When the user clicks the "Vaccines" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Save on Pfizer medications' link
    Then the user should see the link with text "Save on Pfizer medications"
    When the user clicks the "Save on Pfizer medications" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/prescription-assistance"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'COVID-19 & flu' link
    Then the user should see the link with text "COVID-19 & flu"
    When the user clicks the "COVID-19 & flu" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Vaccines overview' link
    Then the user should see the link with text "Vaccines overview"
    When the user clicks the "Vaccines overview" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/vaccine-options"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Check vaccine eligibility' link
    Then the user should see the link with text "Check vaccine eligibility"
    When the user clicks the "Check vaccine eligibility" link 
    Then the user should be navigated to the about page with URL "https://www.vaxassist.com/pfizer-for-all/eligibility?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Schedule a vaccination' link
    Then the user should see the link with text "Schedule a vaccination"
    When the user clicks the "Schedule a vaccination" link 
    Then the user should be navigated to the about page with URL "https://www.vaxassist.com/pfizer-for-all/schedule?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Migraine overview' link
    Then the user should see the link with text "Migraine overview"
    When the user clicks the "Migraine overview" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Migraine science' link
    Then the user should see the link with text "Migraine science"
    When the user clicks the "Migraine science" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine/science"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Migraine treatments' link
    Then the user should see the link with text "Migraine treatments"
    When the user clicks the "Migraine treatments" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine/treatment"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Talk to a doctor now' link
    Then the user should see the link with text "Talk to a doctor now"
    When the user clicks the "Talk to a doctor now" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine/talk-to-a-doctor"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Prior authorization support' link
    Then the user should see the link with text "Prior authorization support"
    When the user clicks the "Prior authorization support" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/prescription-assistance"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Explore a Pfizer migraine treatment option' link
    Then the user should see the link with text "Explore a Pfizer migraine treatment option"
    When the user clicks the "Explore a Pfizer migraine treatment option" link 
    Then the user should be navigated to the about page with URL "https://www.nurtec.com/?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'COVID-19 & flu overview' link
    Then the user should see the link with text "COVID-19 & flu overview"
    When the user clicks the "COVID-19 & flu overview" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/respiratory/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Order at-home tests' link
    Then the user should see the link with text "Order at-home tests"
    When the user clicks the "Order at-home tests" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/respiratory/testing"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Talk to a doctor now' link
    Then the user should see the link with text "Talk to a doctor now"
    When the user clicks the "Talk to a doctor now" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/respiratory/telehealth"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Quick answer guide' link
    Then the user should see the link with text "Quick answer guide"
    When the user clicks the "Quick answer guide" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/respiratory/quick-answer-guide"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Explore a Pfizer COVID-19 treatment option' link
    Then the user should see the link with text "Explore a Pfizer COVID-19 treatment option"
    When the user clicks the "Explore a Pfizer COVID-19 treatment option" link 
    Then the user should be navigated to the about page with URL "https://www.paxlovid.com/?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Support' link
    Then the user should see the link with text "Support"
    When the user clicks the "Support" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/support"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Take health questionnaires' link
    Then the user should see the link with text "Take health questionnaires"
    When the user clicks the "Take health questionnaires" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/healthquestionnaires"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Manage migraine' link
    Then the user should see the link with text "Manage migraine"
    When the user clicks the "Manage migraine" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine/"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Navigate menopause' link
    Then the user should see the link with text "Navigate menopause"
    When the user clicks the "Navigate menopause" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/menopause"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Schedule vaccines' link
    Then the user should see the link with text "Schedule vaccines"
    When the user clicks the "Schedule vaccines" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/vaccine-options"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Get answers to health and wellness questions' link
    Then the user should see the link with text "Get answers to health and wellness questions"
    When the user clicks the "Get answers to health and wellness questions" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/hub#health-wellness"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Learn more about symptoms' link
    Then the user should see the link with text "Learn more about symptoms"
    When the user clicks the "Learn more about symptoms" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/menopause#symptoms_section"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Get started' link
    Then the user should see the link with text "Get started"
    When the user clicks the "Get started" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/respiratory"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for another 'Get started' link
    Then the user should see the link with text "Get started"
    When the user clicks the "Get started" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/migraine"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Ask a question' link
    Then the user should see the link with text "Ask a question"
    When the user clicks the "Ask a question" link 
    Then the user should be navigated to the about page with URL "https://healthanswers.pfizer.com/?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for another 'Get started' link
    Then the user should see the link with text "Get started"
    When the user clicks the "Get started" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/vaccine-options"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for another 'Get started' link
    Then the user should see the link with text "Get started"
    When the user clicks the "Get started" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/prescription-assistance"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Visit Pfizer.com' link
    Then the user should see the link with text "Visit Pfizer.com"
    When the user clicks the "Visit Pfizer.com" link 
    Then the user should be navigated to the about page with URL "https://www.pfizer.com/?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Sign Up for Updates' link
    Then the user should see the link with text "Sign Up for Updates"
    When the user clicks the "Sign Up for Updates" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/sign-up"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for another 'Support' link
    Then the user should see the link with text "Support"
    When the user clicks the "Support" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/support"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Terms of Use' link
    Then the user should see the link with text "Terms of Use"
    When the user clicks the "Terms of Use" link 
    Then the user should be navigated to the about page with URL "https://www.pfizer.com/general/terms?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Privacy Policy' link
    Then the user should see the link with text "Privacy Policy"
    When the user clicks the "Privacy Policy" link 
    Then the user should be navigated to the about page with URL "https://www.pfizer.com/privacy?exitCode=pfa"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Cookie Preferences' link
    Then the user should see the link with text "Cookie Preferences"
    When the user clicks the "Cookie Preferences" link 
    Then the user should be navigated to the about page with URL "https://www.pfizerforall.com/#ot-sdk-btn"

  @link @visibility @interaction
  Scenario: Verify visibility and interaction for the 'Washington Health Data Privacy Policy' link
    Then the user should see the link with text "Washington Health Data Privacy Policy"
    When the user clicks the "Washington Health Data Privacy Policy" link 
    Then the user should be navigated to the about page with URL "https://www.pfizer.com/washington-health-data-privacy-policy?exitCode=pfa"

  @image @visibility @accessibility
  Scenario: Verify visibility of the pause icon
      Then the user should see the image with alt text "pause icon"

  @image @visibility @accessibility
  Scenario: Verify visibility of the smiling women image
      Then the user should see the image with alt text "two women looking forward and smiling together"
      And the image should have the alt text "two women looking forward and smiling together"

  @image @visibility @accessibility
  Scenario: Verify visibility of the cancer questionnaire thumbnail
      Then the user should see the image with alt text "Cancer questionnaire thumbnail"
      And the image should have the alt text "Cancer questionnaire thumbnail"

  @image @visibility @accessibility
  Scenario: Verify visibility of the AFib questionnaire thumbnail
      Then the user should see the image with alt text "AFib questionnaire thumbnail"
      And the image should have the alt text "AFib questionnaire thumbnail"

  @image @visibility @accessibility
  Scenario: Verify visibility of the migraine questionnaire thumbnail
      Then the user should see the image with alt text "Migraine questionnaire thumbnail"
      And the image should have the alt text "Migraine questionnaire thumbnail"

  @image @visibility @accessibility
  Scenario: Verify visibility of the vaccines questionnaire thumbnail
      Then the user should see the image with alt text "Vaccines questionnaire thumbnail"
      And the image should have the alt text "Vaccines questionnaire thumbnail"

  @image @visibility @accessibility
  Scenario: Verify visibility of the family members smiling image
      Then the user should see the image with alt text "Older and younger family members smiling in a diner"
      And the image should have the alt text "Older and younger family members smiling in a diner"

  @image @visibility @accessibility
  Scenario: Verify visibility of the thoughtful woman image
      Then the user should see the image with alt text "Woman resting her head on her chin thoughtfully"
      And the image should have the alt text "Woman resting her head on her chin thoughtfully"

  @image @visibility @accessibility
  Scenario: Verify visibility of the smartphone displaying Health Answers image
      Then the user should see the image with alt text "Hand holding a smartphone displaying Health Answers by Pfizer on the screen"
      And the image should have the alt text "Hand holding a smartphone displaying Health Answers by Pfizer on the screen"

  @image @visibility @accessibility
  Scenario: Verify visibility of the person with a band aid image
      Then the user should see the image with alt text "Person with a band aid on their arm"
      And the image should have the alt text "Person with a band aid on their arm"

  @image @visibility @accessibility
  Scenario: Verify visibility of the hand holding medication image
      Then the user should see the image with alt text "Hand holding medication"
      And the image should have the alt text "Hand holding medication"

  @image @visibility @accessibility
  Scenario: Verify visibility of the empty alt text image
      Then the user should see the image with alt text " "
  ```

  # --- Scenarios for icons ---
  ```gherkin
  @icon @visibility
  Scenario: Verify visibility of the Pfizer for all home page icon
    Then the user should see the icon with aria-label "Pfizer for all home page"

  @icon @visibility
  Scenario: Verify visibility of the Pfizer for all icon
    Then the user should see the icon with aria-label "Pfizer for all home page"

  @icon @visibility
  Scenario: Verify visibility of the asset icon arrow circle
    Then the user should see the icon with aria-label "Icon - asset-icon-arrow-circle"

  @icon @visibility
  Scenario: Verify visibility of the icon arrow circle
    Then the user should see the icon with aria-label "Icon - icon-arrow-circle"

  @icon @visibility
  Scenario: Verify visibility of the timer icon
    Then the user should see the icon with aria-label "Icon - timer"

  @icon @visibility
  Scenario: Verify visibility of the unionsubmit icon
    Then the user should see the icon with aria-label "Icon - unionsubmit-icon"

  @icon @visibility
  Scenario: Verify visibility of the white Pfizer logo icon
    Then the user should see the icon with aria-label "Icon - white-pfizer-logo-currentcolor"

  @icon @visibility
  Scenario: Verify visibility of the Area of Interest icon
    Then the user should see the icon with aria-label "Area of Interest"