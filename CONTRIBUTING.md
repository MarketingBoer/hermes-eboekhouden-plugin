# Contributing

Thanks for improving this independent community plugin.

## Report a problem

Search existing issues first. Include the expected result, actual result, Python version, Hermes version, and reproducible steps. State whether a failure occurs before authentication, during a dry-run, or during a confirmed request.

Use only synthetic examples. Remove tokens, session identifiers, customer records, bank details, personal paths, and private administration data. Do not paste a full environment file or accounting response.

Use [SECURITY.md](SECURITY.md) for possible vulnerabilities.

## Propose a change

Discuss large changes before implementation. Keep a change focused. Preserve the method/path allowlist, strict boolean confirmation, deletion acknowledgement, fixed API origin, blocked redirects, and profile credential isolation unless a separately reviewed policy change requires otherwise.

Add an offline regression test for changed behavior. Use synthetic credentials. Block network access. Do not use an accounting account to run the test suite.

Run the tests with a compatible Hermes Python environment:

```sh
python -B -m unittest discover -s tests -v
```

Update documentation when behavior changes. Describe external effects and remaining limitations. Do not claim live-account compatibility from mocked tests.

## Rights and release status

A license has not yet been selected for this candidate. Do not assume that public visibility grants reuse or redistribution rights. Resolve license and contribution terms before accepting external code for an open-source release. Preserve required attribution.

There is no promised response time or release schedule.
