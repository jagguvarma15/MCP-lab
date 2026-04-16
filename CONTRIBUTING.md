# Contributing to MCP Lab

Thanks for your interest in the project. MCP Lab is a research harness, so most
contributions fall into one of three buckets:

1. **New test scenarios** — exercising a behavior nobody's probed yet
2. **New harness capabilities** — a new `ServerBehaviors` knob, interceptor
   hook, or transport
3. **Docs / findings** — write-ups in `docs/` when a test surfaces something
   worth a narrative

This guide focuses on #1 because the fixture-driven design makes it the fastest
path to useful contributions.

## Setup

```bash
git clone <your fork>
cd mcp-lab
pip install -e ".[all]"        # installs aiohttp, hypothesis, tiktoken
pytest -q                       # baseline: should be all green
```

Python 3.10+ is required. CI matrixes against 3.10, 3.12, and 3.14.

## How a test scenario fits together

Every test is a triangle:

```
  server preset          test class              fixture
  (fixtures/servers/  →  (tests/<area>/      +   (tests/conftest.py
   *.json OR             test_*.py)              if reusable)
   make_server_cmd)
```

- **Server preset** — what misbehavior are we simulating?
- **Test class** — what invariant should hold despite that misbehavior?
- **Fixture** — reusable plumbing (client lifecycle, pre-initialized sessions)

You rarely need all three for a single test. Start with the minimum and promote
upward only when a second test would duplicate code.

## Recipe: add a new test scenario

Suppose you want to test that clients **handle an MCP server whose
`tools/list` always returns a JSON-RPC error**, without raising.

### Step 1 — pick the server misbehavior

Check `harness/mock_server.py:ServerBehaviors` for an existing knob. The
current behaviors cover latency, drops, errors, ID omission, wrong JSON-RPC
versions, extra fields, malformed JSON rate, tool description suffixes, and
tool-name shadowing. If none fit, [add a knob](#adding-a-new-serverbehaviors-knob).

For this example, `error_rate` exists — use it.

### Step 2 — decide: one-off or preset?

**One-off** (used by one test): call `make_server_cmd` from
`tests/conftest.py`. It turns kwargs into CLI flags:

```python
from tests.conftest import make_server_cmd

cmd = make_server_cmd(error_rate=1.0)   # always errors
```

**Preset** (used by multiple tests, or worth a name in isolation): add a JSON
file to `fixtures/servers/`. Presets describe the scenario in words:

```jsonc
// fixtures/servers/always_errors.json
{
  "name": "always-errors",
  "description": "Returns a JSON-RPC error for every request.",
  "behaviors": {
    "error_rate": 1.0
  },
  "tools": ["echo"]
}
```

Loaded via `harness.config.load_server_preset("always_errors")`. See
`harness/config.py` for the full behavior schema — unrecognized keys are
silently ignored, so double-check your spelling.

### Step 3 — write the test

Tests live under `tests/<area>/test_*.py` where `<area>` matches one of the
[markers](#markers-and-selective-runs). Follow the existing pattern: a class
per scenario, one method per invariant, a docstring on the class explaining
*why the behavior matters*.

```python
# tests/integration/test_error_resilience.py
from harness import MockMCPClient
from tests.conftest import make_server_cmd


class TestServerErrorResilience:
    """
    A server may return an error for every request (quota exhausted, auth
    expired, upstream down). The client must surface the error as data and
    keep the session alive, because the surrounding agent loop depends on
    being able to issue the next request.
    """

    def test_error_response_is_surfaced_not_raised(self):
        cmd = make_server_cmd(error_rate=1.0)
        with MockMCPClient.over_stdio(cmd) as client:
            client.initialize()
            resp = client.list_tools()
            assert resp is not None
            assert resp.is_error
            assert resp.error["code"] == -32603
```

### Step 4 — (optional) add a shared fixture

If a second test needs the same setup, lift it into `tests/conftest.py`:

```python
@pytest.fixture
def malformed_client():
    cmd = make_server_cmd(malformed_json_rate=1.0)
    c = MockMCPClient.over_stdio(cmd)
    yield c
    c.shutdown()
```

Don't pre-create fixtures "just in case" — the threshold is two call sites.

### Step 5 — run it

```bash
pytest tests/transport/test_malformed_recovery.py -v
pytest -m "transport and not slow"          # category selector
```

## Markers and selective runs

Markers are auto-applied by `tests/conftest.py` based on path. Users never
have to add `@pytest.mark.<category>` manually.

| Marker | Applied to | When to use |
|--------|-----------|-------------|
| `conformance`, `security`, `fuzz`, `integration`, `transport`, `evaluation` | directory of the test | category selection |
| `slow` | all fuzz tests + `test_high_latency_still_works` | `-m "not slow"` for quick runs |
| `requires_http` | `tests/transport/test_http.py` | skip when aiohttp is absent |
| `requires_hypothesis` | `tests/fuzzing/**` | skip when hypothesis is absent |

If you add a test file that belongs in a new category (say, `tests/interop/`),
update the `_PATH_MARKERS` dict in `tests/conftest.py` and declare the marker
in `pyproject.toml`'s `[tool.pytest.ini_options].markers`.

Common selectors:

```bash
pytest -m "security and not slow"                 # fast security pass
pytest -m "conformance or integration"            # spec + composition
pytest -m "not requires_http and not fuzz"        # minimal deps only
pytest --log-cli-level=DEBUG -k test_ping         # see protocol trace
```

## Adding a new `ServerBehaviors` knob

When no existing knob captures your scenario:

1. Add the field to `ServerBehaviors` in `harness/mock_server.py` with a
   conservative default (the default should be "behave normally").
2. Implement the behavior in `MockMCPServer._process_with_behaviors` (or
   wherever it's relevant in the pipeline).
3. Add a matching CLI flag in the `__main__` block of `harness/mock_server.py`
   so `make_server_cmd(your_knob=...)` works from tests.
4. Add the key to `load_server_preset` in `harness/config.py` so presets can
   use it.
5. Write at least one test that exercises the knob.

Keep knobs orthogonal — combining them should produce predictable superposition
(e.g. `delay_ms=100` + `error_rate=0.5` = half the responses are errors, all
delayed by 100ms). Don't encode compound scenarios as a single knob; build
them from orthogonal ones.

## Writing guidelines

The philosophy section in the README applies; a few practical amplifications:

- **One invariant per test.** If the docstring says "and," split it.
- **Use `MockMCPClient.over_stdio` as a context manager** so subprocesses get
  cleaned up on assertion failure.
- **Don't mock the transport.** The whole point is that we're testing real
  stdio round-trips against a real subprocess.
- **Add a class docstring** explaining the *risk* or *question* — not just
  what the test does. Future readers need the "why."
- **Prefer `logger.debug` over `print`** in harness modules. Test files can
  use `print` freely (pytest captures with `-s`).
- **Record findings in `docs/`** when a test reveals something genuinely
  interesting — a spec gap, an implementation quirk, a performance cliff.

## Running the full suite before submitting

```bash
pytest -q                                    # all 95+ tests
pytest -m "not slow" -q                      # quick feedback loop
python scripts/profile_server.py \
    "python -m harness.mock_server" \
    --output profile.json                    # sanity-check the CLI
```

CI runs `pytest -v --tb=short` on Python 3.10, 3.12, and 3.14. If your change
touches transport code, run against at least 3.10 and 3.14 locally.

## PR checklist

- [ ] `pytest -q` is green locally
- [ ] New markers, if any, declared in `pyproject.toml`
- [ ] New `ServerBehaviors` fields have a sane default and at least one test
- [ ] Test classes have docstrings explaining the risk/question
- [ ] If the change surfaces a finding, mention where it's written up (or open
      a follow-up issue)

## Reporting without patching

Not every observation needs a patch. File an issue with:

- What you observed (client/server versions, transport, OS)
- A minimal reproduction — a command line or a ≤30-line script
- What the spec says (or doesn't say) about the behavior

Issues that surface spec ambiguity are valuable even without a fix; they often
turn into entries in `docs/spec-gaps.md`.
