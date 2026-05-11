# Sam v2 Live Testing Protocol

## Principle

Mock tests are useful, but they do not count as complete feature validation for Sam.

Sam must eventually prove itself against real environments, real projects, and real workflows.

## Live Testing Requirements

Every migrated feature should answer:

- Did it run in a real environment?
- Did it produce logs?
- Did it handle errors correctly?
- Did it return structured results?
- Can failures be reproduced and diagnosed?

## Examples

### Terminal Worker

Real test:

- execute actual terminal command
- capture stdout/stderr
- classify failure if command fails

### Git Worker

Real test:

- inspect real repository state
- create branch
- detect modified files

### Browser Worker

Real test:

- open real webpage
- interact with page
- capture screenshot/logs

### Coding Workflow

Real test:

- modify real project file
- run build/test command
- inspect real errors
- retry if appropriate

## Important

A feature is not production-ready only because unit tests pass.

Sam must succeed in realistic workflows.
