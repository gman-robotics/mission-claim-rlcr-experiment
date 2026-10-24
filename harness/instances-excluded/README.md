# Excluded instances

These SWE-bench Verified instances are kept for reference but are not in the allowlist (`harness/instances/`).
Their gold patch does not resolve under the harness's conservative test-id mapping.

- `pytest-dev__pytest-7432`, `pytest-dev__pytest-7490`: PASS_TO_PASS lists
  `testing/test_skipping.py::TestXFail::test_xfail_raises[(AttributeError,`, a dataset id cut at whitespace that
  matches two distinct nodes (`...[(AttributeError, TypeError)-TypeError-*1 xfailed*]` and
  `...[(AttributeError, TypeError)-IndexError-*1 failed*]`). An ambiguous truncated id is scored
  `AMBIGUOUS_PREFIX` (not passed), so gold cannot resolve.
