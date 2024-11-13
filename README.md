## Introduction

[![codecov](https://codecov.io/gh/test-zeus-ai/testzeus-hercules/branch/main/graph/badge.svg?token=testzeus_hercules_token_here)](https://codecov.io/gh/test-zeus-ai/testzeus-hercules)
[![CI](https://github.com/test-zeus-ai/testzeus-hercules/actions/workflows/main.yml/badge.svg)](https://github.com/test-zeus-ai/testzeus-hercules/actions/workflows/main.yml)

Welcome to **Hercules**, the **world’s first open source testing agent** that’s here to lift your testing burdens with the strength of a mythological hero! Imagine a tool with “assert” capabilities so sharp, it can **navigate the web like a seasoned explorer**—that’s Hercules for you. Whether you’re a tester, SDET, QA maestro, or an automation engineer, we’re putting the **power directly into your hands**, empowering you to conquer even the most complex testing challenges.

At Hercules, we believe that **trustworthy and open source code** is the backbone of innovation. That’s why we’ve built Hercules to be transparent, reliable, and community-driven. 

Our mission? To **democratize and disrupt test automation**, making top-tier testing accessible to everyone, not just the elite few. No more gatekeeping—everyone deserves a hero on their testing team!

But what truly sets Hercules apart is our **community**, which is woven into our very DNA. Born from the original **TestZeus project**, we’ve inherited a legacy of collaboration and excellence. Together, we’re forging a path where every contributor, every user, and every enthusiast plays a pivotal role in shaping the future of test automation.

## Install it from PyPI

```bash
poetry install
```

## Usage

```bash
$ python -m testzeus_hercules
#or
$ hercules
```

## Features

![image](https://github.com/user-attachments/assets/af192e81-5b2e-4cb4-8a7c-0f336647e50b)

### Gherkin In. Results Out.

Hercules makes testing as simple as Gherkin in, results out. Just feed your end-to-end tests in Gherkin format, and watch Hercules spring into action. It takes care of the heavy lifting by running your tests automatically and presenting results in a neat JUnit format. No manual steps, no fuss—just efficient, seamless testing.

### Free and Open Source.

With Hercules, you’re harnessing the power of open source with zero licensing fees. Feel free to dive into the code, contribute, or customize it to your heart’s content. Hercules is as free as it is mighty, giving you the flexibility and control you need. 

### Salesforce Ready

Built to handle the most intricate UIs, Hercules conquers Salesforce and other complex platforms with ease. Whether its complicated DOM or running your SOQL or Apex, Hercules is ready and configurable.

### No Code Required

Say goodbye to complex scripts and elusive locators. Hercules is here to make your life easier with its no-code approach, taking care of the automation of Gherkin features, so you can focus on what matters most—building quality software.

### Multilingual

With multilingual support right out of the box, Hercules is ready to work with teams across the globe. Built to bridge language gaps, it empowers diverse teams to collaborate effortlessly on a unified testing platform.

### Precisely Accurate

Unlike other “tools,” Hercules is designed to handle complex, multi-layered testing scenarios with precision. Thanks to its intelligent, multi-agentic design, it consistently delivers accurate, reliable results, ensuring no corner is left untested.

### No Maintenance

Autonomous and adaptive, Hercules takes care of itself with auto-healing capabilities. Forget about tedious maintenance—Hercules adjusts to changes and stays focused on achieving your testing goals.

### UI Assertions

Grounded in the powerful foundations of TestZeus, Hercules tackles UI assertions with unwavering focus, ensuring that no assertion goes unchecked and no bug goes unnoticed. It’s thorough, it’s sharp, and it’s ready for action.

### CI/CD Ready

Run Hercules locally or integrate it seamlessly into your CI/CD pipeline. Docker-native and one-command ready, Hercules fits smoothly into your deployment workflows, keeping testing quick, consistent, and hassle-free.

With Hercules, testing is no longer just a step in the process—it’s a powerful, streamlined experience that brings quality to the forefront.

## Contact us

Join us at our [Discord server](https://discord.gg/4fyEMWVD). 

## Examples

* Salesforce examples
* Puma example

## Contribution 

Read the [CONTRIBUTING.md](CONTRIBUTING.md) file.
* Bounty program - Stay tuned. 😀

## Architecture

### Multi agentic solution

Memory

Architecture

## Opinions

We believe that great quality comes from opinions about a product. So we have incorporated a few of our opinions into the product design. We welcome the community to question them, use them or build on top of them. Here are some examples: 

1. Gherkin is a good enough format for Agents: Gherkin provides a semi-structured format for the LLMs/AI Agents to follow test intent, and user instructions. It provides the right amount of grammar (verbs like Given, When, Then etc) for humans to frame a scenario and agents to follow the instructions. 
2. Tests should be atomic in nature: Software tests should be atomic because it ensures that each test is **focused, independent, and reliable**. Atomic tests target one specific behavior or functionality, making it easier to pinpoint the root cause of failures without ambiguity. Here’s a good example (Atomic Test): 

    Feature: User Login 

    Scenario: Successful login with valid credentials 

    Given the user is on the login page 

    When the user enters valid credentials 

    And the user clicks the login button 

    Then the user should see the dashboard. 

    A non-atomic test confuses both the tester and AI agent. 

3. Open core and open source: Hercules is built on an **open-core model**, combining the spirit of open source with the support and expertise of a commercial company, **TestZeus**. By providing Hercules as open source (licensed under AGPL v3), TestZeus is committed to empowering the testing community with a robust, adaptable tool that’s freely accessible and modifiable. Open source offers transparency, trust, and collaborative potential, allowing anyone to dive into the code, contribute, and shape the project’s direction. Meanwhile, as a commercial entity, TestZeus brings dedicated resources, continuous improvements, and professional support options to ensure Hercules meets the high standards required for enterprise-grade testing. This approach allows Hercules to evolve and thrive as a community-driven project while benefiting from the reliability and ongoing innovation that TestZeus brings to the table.

## Tools

* Available tools: Hercules has a host of tools available at its disposal, to complete any complex testing scenario. These range from clicking on buttons, to piercing shadow DOMs, and running APIs. The complete list can be found here <<Link to tools source code>>
* Building tools: In case you need a certain tool for a nuanced use case, you can also easily build and attach tools by following the instructions here: <<instructions>>

## Evaluations

We wanted to ensure that Hercules stands up to the task of end-to-end testing with immense precision, so we have run Hercules through a wide range of tests such as running APIs, interacting with complex UI scenarios, clicking through calendars or iframes. A full list of evaluations can be found here: <<Link to tests folder>>

## Token Usage

Hercules is an AI native solution, and relies on LLMs to perform the reasoning and actions. Based on our experiments, we have found that a complex use case as below could cost up to $3 using OpenAI’s APIs: 

```
Feature: Account Creation in Salesforce

 Scenario: Successfully create a new account

   Given I am on the Salesforce login page

   When I enter my username "user@example.com" and password "securePassword"

   And I click on the "Log In" button

   And I navigate to the "Accounts" tab

   And I click on the "New" button

   And I fill in the "Account Name" field with "Test Account"

   And I select the "Account Type" as "Customer"

   And I fill in the "Website" field with "www.testaccount.com"

   And I fill in the "Phone" field with "123-456-7890"

   And I click on the "Save" button

   Then I should see a confirmation message "Account Test Account created successfully"

   And I should see "Test Account" listed in the account records
```

## Difference from other “tools”

Hercules isn't just another testing tool—it's an “agent” of change. Powered by synthetic intelligence that can **think, reason, and react** based on requirements, Hercules goes beyond simple automation scripts. We bring an industry-first approach to open source agents. This means faster, smarter, and more resilient testing cycles, especially for complex platforms. With **industry-leading performance** and a fully open-source foundation, TestZeus combines powerful capabilities with community-driven flexibility, making top-tier testing accessible and transformative for everyone.

## High level roadmap

## Open Core and Open Source

TestZeus operates as an open core company, blending open-source and proprietary components to deliver a robust software testing platform. At the heart of its open-source offering is Hercules, a powerful tool designed to autonomously execute tests using tools such as browsers or APIs, enabling faster and more reliable testing processes for developers and QA teams. By open-sourcing Hercules, TestZeus invites contributions from the community while offering the testing platform with other agents, as a commercial product. This open-core approach allows TestZeus to drive innovation and foster a collaborative ecosystem, empowering companies to build quality software with agility and transparency.

## Credits

Hercules would have not been possible without the great work from below sources: 

1. [Agent-E](https://arxiv.org/abs/2407.13032)
2. [Q Star](https://arxiv.org/abs/2312.10868)
3. [Agent Q](https://arxiv.org/abs/2408.07199)

*Note* - You can find the legacy TestZeus repo [here](https://www.testzeus.org)
