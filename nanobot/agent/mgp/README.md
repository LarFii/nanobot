# MGP Sidecar for nanobot

Optional integration that connects nanobot to a [Memory Governance Protocol
(MGP)](https://github.com/hkuds/MGP) gateway. When enabled, the agent can
explicitly recall cross-session, governed long-term memory via the
`recall_memory` tool, and the LLM-extracted facts produced by nanobot's
existing Consolidator and Dream pipelines are mirrored to MGP automatically.

> **TL;DR** — disabled by default; opt in with `agents.defaults.mgp.enabled: true`.
> Recall is **agent-driven** (a tool, not a system-prompt injection).
> Commit is **automatic** (rides on Consolidator + Dream output, zero added LLM cost).

---

## 1. Why MGP (vs nanobot's native bulk-injection memory)

nanobot's native memory pipeline already does excellent work for stable,
single-instance deployments:

- `MEMORY.md` / `SOUL.md` / `USER.md` are **bulk-injected** into every system
  prompt — the agent never has to "ask" for them.
- `Dream` periodically condenses raw history into curated long-term knowledge.
- `Consolidator` summarizes evicted messages into `history.jsonl` so the agent
  can `grep` them on demand.

MGP is a **complementary** layer, not a replacement:

| Dimension     | nanobot native                       | MGP sidecar (this package)        |
| ------------- | ------------------------------------ | --------------------------------- |
| Trigger       | Every LLM request, automatic         | Only when the agent calls a tool  |
| Method        | Bulk inject curated full files       | Query-based recall via tool call  |
| Query         | None (everything is always present)  | Agent-constructed search topic    |
| Frequency     | 100% of turns                        | Typically <20% of turns           |
| Best at       | "What the agent always should know"  | "What is relevant to this turn"   |

**The two stack**:

```
SOUL.md / MEMORY.md / USER.md   ← native bulk injection (always-on)
                                +
recall_memory tool (when needed) ← agent-driven targeted recall
```

Add MGP when you have at least one of:

- Multi-device / multi-channel sync (agent should remember preferences across CLI ↔ Telegram ↔ web)
- Multiple users sharing the same bot (group chats, customer-support style)
- Long history where `grep` over `history.jsonl` becomes too slow
- Cross-language / synonym-tolerant recall (requires a vector backend)
- Compliance / audit logging requirements

Skip MGP when you're a single-user, single-language, single-channel deployment
— native memory is already enough.

---

## 2. How It Works

### Recall path (agent decides)

```mermaid
flowchart TD
  UserMsg["InboundMessage"] --> ProcessMessage["AgentLoop._process_message (unchanged)"]
  ProcessMessage --> BuildMsgs["context.build_messages (unchanged)"]
  BuildMsgs --> SystemPrompt["system prompt includes mgp-memory SKILL.md"]
  SystemPrompt --> LLMCall["LLM reasoning"]
  LLMCall --> Decide{"Need to recall?"}
  Decide -->|"no (~80% of turns)"| Answer["Direct answer"]
  Decide -->|"yes"| ToolCall["tool_call: recall_memory(query, scope?, limit?, types?)"]
  ToolCall --> ToolExec["MGPRecallTool.execute"]
  ToolExec --> Sidecar["sidecar.recall(runtime, intent)"]
  Sidecar --> Gateway["MGP /search"]
  Gateway --> Result["top-K results"]
  Result --> ToolReturn["Formatted string back to LLM"]
  ToolReturn --> LLMCall
```

### Commit path (automatic, agent does not participate)

```mermaid
flowchart TD
  AgentRun["AgentLoop._run_agent_loop"] --> SaveTurn["session.save"]
  SaveTurn --> ConsolBg["Consolidator.maybe_consolidate_by_tokens (background)"]
  ConsolBg --> Archive["Consolidator.archive(messages, session=...)"]
  Archive --> AppendHistory["store.append_history(summary)"]
  AppendHistory --> CommitB{"sidecar enabled?"}
  CommitB -->|"yes"| ParseBullets["parse_consolidator_bullets"]
  ParseBullets --> WriteMGP1["asyncio.create_task: sidecar.commit_bullets(...)"]

  CronTick["Cron 2h"] --> DreamRun["Dream.run"]
  DreamRun --> Phase1["LLM Phase-1 [USER]/[MEMORY]/[SOUL] tags"]
  Phase1 --> CommitC{"sidecar enabled?"}
  CommitC -->|"yes"| ParseTags["parse_dream_phase1_tags"]
  ParseTags --> WriteMGP2["asyncio.create_task: sidecar.commit_dream_tags(...)"]
  Phase1 --> Phase2["Dream Phase 2 file edits (unchanged)"]
```

---

## 3. What Gets Written, Who Controls It

Two — and only two — channels write to MGP:

| Channel                  | Trigger                                            | Content written                                                                                | Toggle                              |
| ------------------------ | -------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------- |
| B — Consolidator bullets | Token budget exceeded / autocompact / `/new`       | LLM-extracted bullets from `consolidator_archive.md` — one `MemoryCandidate` per bullet        | `mgp.enable_consolidator_commit`    |
| C — Dream Phase-1 tags   | Cron every 2h / `/dream`                           | LLM-extracted `[USER]` / `[MEMORY]` / `[SOUL]` lines from `dream_phase1.md`                    | `mgp.enable_dream_commit`           |

**Never written to MGP**: raw user messages, raw assistant replies, tool call
results, LLM `<thinking>`, `history.jsonl` entries themselves, the contents of
`MEMORY.md` / `SOUL.md` / `USER.md`, media attachments, API keys, secrets.

**Outbound during recall**: when the agent calls `recall_memory`, the **agent-
constructed `query` string** (a concise topic, per the SKILL.md guidance) is
sent to the gateway. This is not the user's raw message — see
[Privacy Notes](#12-privacy-notes).

---

## 4. What Doesn't Change

When MGP is enabled, **all native memory behaviors stay exactly as-is**:

- `MEMORY.md`, `SOUL.md`, `USER.md` continue to be bulk-injected into the
  system prompt every turn.
- `history.jsonl` continues to be the source of truth for `grep`-based
  history lookups by the agent.
- `Dream` continues to edit local files and commit via git.
- `autocompact` continues to compress idle sessions.
- All slash commands (`/dream`, `/dream-log`, `/dream-restore`, ...) work
  unchanged.

If MGP is disabled (the default) or unreachable at runtime, the agent behaves
**byte-identically** to a build without MGP.

---

## 5. Quickstart (PostgreSQL backend)

Four steps, all PyPI:

```bash
# 1. Install nanobot's MGP client extra (~one extra dep)
pip install "nanobot[mgp]"

# 2. Install the MGP gateway with the postgres adapter extra
pip install "mgp-gateway[postgres]>=0.1.1"

# 3. Run a Postgres for the gateway to use
docker run -d --name mgp-pg \
  -e POSTGRES_USER=mgp -e POSTGRES_PASSWORD=mgp -e POSTGRES_DB=mgp \
  -p 5432:5432 postgres:16

# 4. Start the gateway
MGP_ADAPTER=postgres \
MGP_POSTGRES_DSN='postgresql://mgp:mgp@127.0.0.1:5432/mgp' \
mgp-gateway
```

Then enable MGP in your nanobot config:

```yaml
agents:
  defaults:
    mgp:
      enabled: true
```

Restart nanobot. Use `/mgp-status` to verify the connection. Ask the agent
something that references past context (e.g. "what's my preferred indentation?")
to see it call `recall_memory`.

---

## 6. Configuration Reference (nanobot side)

All fields live under `agents.defaults.mgp` in nanobot config.

| Field                          | Default                       | Purpose                                                              |
| ------------------------------ | ----------------------------- | -------------------------------------------------------------------- |
| `enabled`                      | `false`                       | Master switch. Off = no MGP code path runs at all.                   |
| `gateway_url`                  | `http://127.0.0.1:8080`       | MGP gateway HTTP base URL.                                           |
| `timeout`                      | `5.0`                         | Per-request timeout (seconds).                                       |
| `fail_open`                    | `true`                        | Swallow MGP errors so a degraded gateway never breaks a turn.         |
| `workspace_as_tenant`          | `true`                        | Use the workspace path as `tenant_id` when `tenant_id` is unset.     |
| `tenant_id`                    | `null`                        | Explicit tenant id. Wins over `workspace_as_tenant`.                 |
| `actor_agent`                  | `nanobot/main`                | Identity recorded in MGP's audit log.                                |
| `api_key`                      | `null`                        | Bearer token (for gateways with `auth_mode=api_key`).                |
| `enable_consolidator_commit`   | `true`                        | Mirror Consolidator bullets to MGP.                                  |
| `enable_dream_commit`          | `true`                        | Mirror Dream Phase-1 tags to MGP.                                    |
| `recall_default_scope`         | `"user"`                      | Default `scope` when the agent omits it in `recall_memory(...)`.     |
| `recall_default_limit`         | `5`                           | Default `limit` when the agent omits it. Capped at 20.               |

There is **no `mode` / `shadow` field** — recall is agent-driven, so there is
no auto-injection vs no-injection toggle. Granular control is through the
two `enable_*_commit` flags.

---

## 7. Configuration Reference (gateway side)

Adapter selection lives **on the MGP gateway side**, not in `nanobot.yaml`.
nanobot only needs to know `gateway_url` (and optionally `api_key`).

Production-grade adapters:

| Adapter      | Install                                            | Required env                                                                                                         |
| ------------ | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `postgres`   | `pip install "mgp-gateway[postgres]>=0.1.1"`       | `MGP_ADAPTER=postgres`, `MGP_POSTGRES_DSN=...`                                                                        |
| `oceanbase`  | `pip install "mgp-gateway[oceanbase]>=0.1.1"`      | `MGP_ADAPTER=oceanbase`, `MGP_OCEANBASE_DSN=...`                                                                      |
| `lancedb`    | `pip install "mgp-gateway[lancedb]>=0.1.1"`        | `MGP_ADAPTER=lancedb`, `MGP_LANCEDB_DIR=...`, `MGP_LANCEDB_EMBEDDING_PROVIDER`, `MGP_LANCEDB_EMBEDDING_MODEL`, `MGP_LANCEDB_EMBEDDING_API_KEY` |
| `mem0`       | `pip install mem0ai`                               | `MGP_ADAPTER=mem0`, `MGP_MEM0_API_KEY=...`                                                                           |
| `zep`        | `pip install zep-cloud`                            | `MGP_ADAPTER=zep`, `MGP_ZEP_API_KEY=...`                                                                             |

Reference adapters (`memory`, `file`, `graph`) are for protocol verification —
do not use in production.

Authentication on the gateway side:

```bash
export MGP_GATEWAY_AUTH_MODE=api_key   # off / api_key / bearer
export MGP_GATEWAY_API_KEY=secret-...  # set the same value as nanobot.yaml mgp.api_key
mgp-gateway
```

See the [MGP repository](https://github.com/hkuds/MGP) for the full set of
gateway environment variables.

---

## 8. Adapter Selection Guide

### By user profile

| Your situation                                          | Should you enable MGP? | Recommended backend       |
| ------------------------------------------------------- | ---------------------- | ------------------------- |
| Single-user, single-language, single-channel            | No                     | —                         |
| Multi-device sync, agent should remember me everywhere  | Yes                    | `postgres`                |
| One bot serving multiple users (groups, support)        | Yes                    | `postgres` (subject-isolated) |
| Frequent cross-language / synonym recall                | Yes                    | `lancedb` or `mem0`       |
| Large history (thousands of entries), `grep` is slow    | Yes                    | `lancedb`                 |
| Compliance / audit / GDPR-style data governance         | Yes                    | `postgres` + audit log    |
| Already using mem0 or zep                               | Yes                    | `mem0` / `zep`            |
| Cannot tolerate >1s first-token latency                 | Avoid SaaS backends    | `postgres` / local `lancedb` |

### Retrieval behavior comparison (same data set)

| Dimension                | nanobot native             | MGP postgres            | MGP lancedb               | MGP mem0    | MGP zep              |
| ------------------------ | -------------------------- | ----------------------- | ------------------------- | ----------- | -------------------- |
| Search method            | None (full bulk inject)    | SQL ILIKE / JSONB GIN   | Vector ANN (+ FTS hybrid) | Vector + graph | Vector + episode graph |
| Ranks by relevance       | No                         | Yes (lexical score)     | Yes (cosine)              | Yes         | Yes                  |
| Cross-language matching  | Only after Dream normalizes | No                      | Yes                       | Yes         | Yes                  |
| Synonym recall           | Only after Dream normalizes | No                      | Yes                       | Yes         | Yes                  |
| Multi-user isolation     | No (single MEMORY.md)      | Yes (subject filter)    | Yes                       | Yes         | Yes                  |
| Cross-session sharing    | No (per-workspace)         | Yes (within tenant)     | Yes                       | Yes         | Yes                  |
| Recall latency           | 0 (no recall step)         | ~50–100 ms              | ~50–200 ms                | ~8–15 s     | ~1–2 s               |
| Curation quality         | Dream LLM (best)           | Raw bullets             | Raw bullets               | Light dedupe | Light dedupe         |

---

## 9. `recall_memory` Tool Usage

The agent sees the [`mgp-memory` skill](../../skills/mgp-memory/SKILL.md)
in every system prompt. That skill teaches it when to call the tool. From
the agent's perspective:

```python
recall_memory(
    query="indentation preference",       # required: concise topic, NOT a full question
    scope="user",                          # optional: "user" / "agent" / "session"
    limit=5,                               # optional: 1..20
    types=["preference"],                  # optional: filter by memory types
)
```

Returns a string like:

```
- [preference] User prefers 4-space indentation for Python.
- [preference] User prefers concise replies, no preamble.
```

…or `(no memories found)` when the search is empty, or
`[recall_memory degraded: <code>]` when the gateway failed (fail-open path).

When NOT to call:

- Information is already in your system prompt (`MEMORY.md` / `SOUL.md` / `USER.md` / Recent History)
- General knowledge questions
- Pure code/tool tasks with no user-specific context
- You already called `recall_memory` this turn and got results

Full guidance lives in
[`nanobot/skills/mgp-memory/SKILL.md`](../../skills/mgp-memory/SKILL.md).

---

## 10. Slash Commands

`/mgp-status` shows:

- `enabled` flag and gateway URL
- whether the `recall_memory` tool is registered
- which commit channels are on
- the most recent recall outcome (query, latency, hit count, error if any)
- the most recent commits (last 32 kept, with success/failure breakdown)

When MGP is disabled, `/mgp-status` returns a one-liner pointing here.

---

## 11. Trade-offs

### Latency

| Backend          | Recall round-trip   | Notes                                    |
| ---------------- | ------------------- | ---------------------------------------- |
| `postgres` local | ~50–100 ms          | Dominated by SQL plan + JSON parse       |
| `lancedb` local  | ~50–200 ms          | Vector ANN + (optional) FTS hybrid       |
| `mem0` SaaS      | ~8–15 s             | Through MGP HTTP → SaaS round-trip       |
| `zep` SaaS       | ~1–2 s              | Same path; Zep is faster on the wire     |

A recall call adds at most one extra LLM round-trip (the agent decides → tool
runs → agent reasons again). The agent only pays this cost when it judges
recall to be useful — typically ~20% of turns.

### Reliability

The sidecar is **fail-open** by default: any MGP error (HTTP timeout, gateway
500, schema validation failure) is swallowed and surfaced to the agent as
`[recall_memory degraded: <code>]`. The conversation never dies because of an
MGP problem.

### Misses

The agent might forget to call `recall_memory` when it would have helped. The
SKILL.md trigger word list ("remember", "I told you", "我之前说过", "还记得") is
designed to minimize this, but it's not zero. If you observe specific topics
that the agent should always check on, extend the SKILL.md examples.

### Duplication

The same fact may live both in `MEMORY.md` (Dream-curated, locally stored) and
in MGP (raw bullets). This is intentional — local files stay the source of
truth for the bulk-injected context, and MGP stays the source of truth for
on-demand cross-session recall. Stage 2 may add `[FILE-REMOVE] →
expire_memory` propagation to keep them in sync.

---

## 12. Privacy Notes

When the agent calls `recall_memory`, the **agent-constructed `query` string**
(per the SKILL.md guidance — a topic, not a full user question) is sent to the
configured gateway over HTTP.

- **Local backends (`postgres`, `lancedb`, `oceanbase`)**: query stays on your
  network — never leaves the gateway process.
- **SaaS backends (`mem0`, `zep`)**: the gateway forwards the query to the
  vendor's API, where it is processed under that vendor's privacy policy.

For the **commit** path, only LLM-extracted bullets / tags are written. Raw
conversation text is never sent. See [§3](#3-what-gets-written-who-controls-it)
for the exhaustive list of what can and cannot be written.

When `mgp.enabled=false` (default), nanobot does not import `mgp_client` and
does not contact any gateway — full air-gap.

---

## 13. Troubleshooting

### `/mgp-status` says "MGP sidecar not enabled"

Check that:
- `mgp.enabled: true` is set in your config under `agents.defaults`.
- You restarted nanobot after editing the config.

### Tool exists but every call shows `[recall_memory degraded: ...]`

- Confirm the gateway is running: `curl http://127.0.0.1:8080/mgp/capabilities`
- Confirm `mgp.gateway_url` matches the gateway's bind address.
- If the gateway requires auth, set `mgp.api_key` AND
  `MGP_GATEWAY_API_KEY` to the same value, with `MGP_GATEWAY_AUTH_MODE=api_key`.

### Agent never calls `recall_memory` even when relevant

- Confirm the `mgp-memory` skill is loaded (it should appear in the system
  prompt — check via debug logs or `/status`).
- Strengthen SKILL.md trigger words for your specific use case (the file is at
  `nanobot/skills/mgp-memory/SKILL.md` — extending it is the canonical fix).
- Test by saying something with a strong trigger: "remember when I said I
  prefer X?" — this should reliably elicit a recall.

### `ModuleNotFoundError: mgp_client`

You enabled `mgp.enabled` without installing the optional dependency. Run:

```bash
pip install "nanobot[mgp]"
```

The error message from `build_sidecar` includes this exact hint.

### Group chat shows the same memory across users

Known limitation: routing context (`_set_tool_context`) does not currently
include `sender_id`, so all members of a group chat share the same `chat_id`
as their `user_id`. 1:1 channels (CLI, Telegram DM, Discord DM) are isolated
correctly. Tracking a future upstream change to expose `sender_id` to tools
would close this gap.

### Latency spike after enabling MGP with a SaaS backend

`mem0` and `zep` round-trips through the gateway are 8–15 s and 1–2 s
respectively. If first-token latency matters, switch to `postgres` (~100 ms)
or local `lancedb` (~200 ms). Use `/mgp-status` to monitor `last_recall.latency`.
