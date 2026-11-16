# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue or pull request.

Use GitHub's private vulnerability reporting: on the repository page choose **Security**, then
**Report a vulnerability**. Include what you found, how to reproduce it, and the version or commit.
You will get an acknowledgement, and a fix or an explanation, as soon as the maintainer can manage;
this is a personal project, so there is no service-level promise.

## This repository holds synthetic data only

Finance360 is software for a household's own finances. Everything in this repository, including
tests, fixtures and the sample household, is invented. No real accounts, statements, names or
figures are committed, and checks in CI and in the commit hooks reject changes that look like them.
If you find something that looks real, report it privately as above.
