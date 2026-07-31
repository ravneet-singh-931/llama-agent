name: Bug Report
about: Report a bug or issue
title: "[Bug] "
labels: bug
body:
  - type: textarea
    attributes:
      name: Describe the bug
      description: A clear and concise description of what the bug is.
    validations:
      required: true
  - type: textarea
    attributes:
      name: To Reproduce
      description: Steps to reproduce the behavior.
      render: bash
      value: |
        1. docker-compose up -d
        2. Go to '...'
        3. Click on '....'
        4. See error
    validations:
      required: true
  - type: textarea
    attributes:
      name: Expected behavior
      description: A clear and concise description of what you expected to happen.
    validations:
      required: true
  - type: textarea
    attributes:
      name: Environment
      description: |
        - OS: [e.g. macOS, Windows, Linux]
        - Docker version: [e.g. 24.0.0]
        - Browser: [e.g. Chrome, Firefox]
      render: yaml
      value: |
        OS:
        Docker:
        Browser:
    validations:
      required: true
  - type: textarea
    attributes:
      name: Additional context
      description: Add any other context about the problem here.
