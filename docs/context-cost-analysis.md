# Context Cost Analysis

Quantitative findings from evaluation tests measuring the real token cost
of MCP tool descriptions in the model's context window.

---

## Token Cost by Complexity Tier

Measurements from `tests/evaluation/test_context_cost.py` using a rough
estimate of ~4 characters per token.


| Tool Tier | Params | Description Length | Approx Chars | Approx Tokens |
| --------- | ------ | ------------------ | ------------ | ------------- |
| Minimal   | 0      | Short (5 chars)    | ~70          | ~18           |
| Simple    | 1      | One line           | ~150         | ~38           |
| Realistic | 5      | Two sentences      | ~600         | ~150          |
| Complex   | 20     | Full paragraph     | ~2500        | ~625          |


Each tool's schema is serialized as JSON and included in the model's context.
The overhead includes field names, type annotations, description text, and
JSON structure characters.

---

## Cost Scaling: N Servers x M Tools

The total context cost scales linearly with the number of tools.
With typical server configurations:


| Configuration                    | Tools | Estimated Tokens | % of 200K Window |
| -------------------------------- | ----- | ---------------- | ---------------- |
| 1 server, 3 tools                | 3     | ~120             | 0.06%            |
| 3 servers, 5 tools each          | 15    | ~600             | 0.30%            |
| 5 servers, 5 tools each          | 25    | ~1000            | 0.50%            |
| 5 servers, 10 tools each         | 50    | ~2000            | 1.00%            |
| 10 servers, 10 tools each        | 100   | ~4000            | 2.00%            |
| Enterprise: 20 servers, 20 tools | 400   | ~16000           | 8.00%            |


At enterprise scale (400 tools), tool descriptions alone consume 8% of
a 200K context window. With verbose descriptions, this can exceed 20%.

---

## Verbosity Tax

Comparing concise vs verbose descriptions for the same tool:


| Style    | Example                                               | Tokens                  |
| -------- | ----------------------------------------------------- | ----------------------- |
| Concise  | "Send an email."                                      | ~38                     |
| Verbose  | Full paragraph with caveats, RFC refs, format details | ~120                    |
| Overhead |                                                       | +82 tokens (~216% more) |


The verbosity tax compounds across tools. A 25-tool setup with verbose
descriptions costs ~2000 extra tokens compared to concise descriptions.

---

## Schema Design Patterns

### Flat vs Nested Parameters


| Pattern                  | Params | Tokens | Notes                                    |
| ------------------------ | ------ | ------ | ---------------------------------------- |
| Flat (7 separate params) | 7      | ~180   | Explicit, more tokens                    |
| Nested (3 object params) | 3      | ~120   | Compact but requires object construction |
| Savings                  |        | ~60    | Nested saves ~33%                        |


Nested parameters reduce token count but require the model to construct
JSON objects, which can reduce call accuracy for complex schemas.

### Many Small Tools vs Few Large Tools


| Pattern                     | Count | Tokens | Notes                     |
| --------------------------- | ----- | ------ | ------------------------- |
| 10 small focused tools      | 10    | ~450   | Clear intent per tool     |
| 2 large multi-purpose tools | 2     | ~250   | Fewer tools, action param |
| Savings                     |       | ~200   | Large tools save ~44%     |


Fewer large tools save tokens but reduce call accuracy because the model
must additionally select the correct `action` parameter.

---

## Recommendations for Tool Authors

1. **Keep descriptions concise.** One sentence is usually sufficient. The model
  does not need RFC references or compliance notes in tool descriptions.
2. **Minimize parameter count.** Each parameter adds ~30-50 tokens of overhead.
  Consider using a single structured input object for tools with many parameters.
3. **Avoid redundant descriptions.** If the parameter name is self-explanatory
  (e.g., `query`, `id`), the description can be minimal or omitted.
4. **Use enums sparingly.** Each enum value adds tokens. Consider if free-text
  input with validation would be more token-efficient.
5. **Consider context budget.** If your server exposes 20+ tools, the cumulative
  token cost is significant. Consider splitting into multiple focused servers
   that can be connected only when needed.
6. **Measure your cost.** Run `tests/evaluation/test_context_cost.py` with your
  real tool schemas to see the actual token impact.

