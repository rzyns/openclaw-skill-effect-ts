# Effect-TS

Effect-TS is a TypeScript library for building type-safe, composable, production-grade programs. It provides structured concurrency, typed errors, dependency injection, resource safety, and observability — all encoded in the type system.

**Documentation:** https://effect.website/docs  
**API reference:** https://effect-ts.github.io/effect/docs/effect  
**LLM-optimised docs:** https://effect.website/llms-full.txt  
**Effect AI:** https://effect.website/docs/ai/introduction/

---

## The Type: `Effect<A, E, R>`

Every Effect program is a value of type `Effect<A, E, R>`:

| Parameter | Meaning |
|-----------|---------|
| `A` | Success value type |
| `E` | Expected (typed) error type |
| `R` | Requirements — services this effect needs from the environment |

```ts
const program: Effect.Effect<string, HttpError, DatabaseService>
// Succeeds with string
// Can fail with HttpError (expected, tracked, recoverable)
// Requires DatabaseService to be provided
```

An Effect is a **description** of a program, not its execution. It does nothing until run.

---

## Type-Level Programming — The Core Discipline

> **This is the most important section. Effect's power lives entirely in the type system. Resist the urge to collapse it.**

Effect encodes program semantics — success types, failure modes, dependencies — at the **type level**. This is not optional style; it is the design. Violating it means losing the guarantees Effect provides.

### The error channel is documentation

```ts
// ❌ Wrong — collapses typed errors into unknown, loses information
const getUser = (id: string): Effect.Effect<User, unknown, never> =>
  Effect.tryPromise(() => fetchUser(id))

// ✅ Right — the error type is part of the contract
class UserNotFound extends Data.TaggedError("UserNotFound")<{ id: string }> {}
class DatabaseError extends Data.TaggedError("DatabaseError")<{ cause: unknown }> {}

const getUser = (id: string): Effect.Effect<User, UserNotFound | DatabaseError, never> =>
  Effect.tryPromise({
    try: () => fetchUser(id),
    catch: (e) => new DatabaseError({ cause: e })
  })
```

### The requirements channel is your dependency graph

```ts
// ❌ Wrong — hides dependencies, makes testing impossible
const processJob = (id: string) =>
  Effect.gen(function* () {
    const db = new Database()        // hardcoded — untestable
    const record = yield* db.find(id)
    return record
  })

// ✅ Right — dependencies declared in R, injectable, testable
const processJob = (id: string) =>
  Effect.gen(function* () {
    const db = yield* Database       // `Database` is a Tag; injected via Layer
    const record = yield* db.find(id)
    return record
  })
// type: Effect<Record, NotFoundError, Database>
```

### Never use `as` or `as unknown as` to escape the type system

If you find yourself casting to make types work, stop. Rethink the data model or error type. The cast silently destroys the guarantees you're building for.

### Preserve union types — do not collapse them

```ts
// ❌ Collapses the union
type JobError = Error

// ✅ Preserves discriminated union — pattern match on these
type JobError = NotFoundError | ValidationError | DatabaseError | NetworkError
```

---

## Two Kinds of Errors

| Kind | Channel | Tracked in types? | Example |
|------|---------|-------------------|---------|
| **Expected errors** (failures) | `E` | Yes | `UserNotFound`, `ValidationError` |
| **Unexpected errors** (defects) | outside `E` | No (runtime only) | `TypeError`, null deref |

Use `Data.TaggedError` for all expected errors. This gives you:
- Discriminated union membership (pattern match safely)
- Structured context fields
- Stack traces
- Type-level tracking

```ts
class PlaneFetchError extends Data.TaggedError("PlaneFetchError")<{
  readonly statusCode: number
  readonly url: string
}> {}
```

**Never** use `Effect.die` or `Effect.sandbox` to escape typed errors unless you have a specific reason.

---

## Effect.gen — Preferred Style for Business Logic

Use `Effect.gen` with `yield*` for sequential logic. It reads like async/await but carries full type safety:

```ts
const dispatchJob = (issueId: string) =>
  Effect.gen(function* () {
    const plane = yield* PlaneClient         // inject service
    const issue = yield* plane.getIssue(issueId)
    const session = yield* spawnAgent(issue)
    yield* JobStore.claim(issueId, session.key)
    return session
  })
// Type inferred: Effect<Session, PlaneError | SpawnError | StoreError, PlaneClient | JobStore>
```

**Rules for `Effect.gen`:**
- Always `function*` (not arrow function `() =>` — generators require `function*`)
- Always `yield*` (not `yield`) — the `*` is mandatory
- Return the final value normally; do not `yield*` it
- Use standard control flow (`if/else`, `for`, `while`) freely inside generators

---

## pipe vs Effect.gen

| Use `pipe` | Use `Effect.gen` |
|-----------|-----------------|
| Single-step transforms | Multi-step sequential logic |
| Combinator chains (`map`, `flatMap`, `tap`) | Complex branching, loops |
| Short, readable transforms | When you need intermediate bindings |

```ts
// pipe — good for simple transforms
const doubled = pipe(Effect.succeed(5), Effect.map(n => n * 2))

// gen — good for multi-step with state
const program = Effect.gen(function* () {
  const n = yield* Effect.succeed(5)
  if (n > 3) return yield* Effect.fail(new TooBig())
  return n * 2
})
```

---

## Services and Layers

Effect's dependency injection is entirely type-level. Services are declared via `Context.GenericTag`; implementations are wired via `Layer`.

### Defining a service

```ts
import { Context, Effect, Layer } from "effect"

// 1. Define the service interface
interface PlaneClient {
  readonly getIssue: (id: string) => Effect.Effect<Issue, PlaneError>
  readonly patchIssue: (id: string, patch: IssuePatch) => Effect.Effect<void, PlaneError>
}

// 2. Create the Tag — this is the DI key
const PlaneClient = Context.GenericTag<PlaneClient>("PlaneClient")

// 3. Create a Layer with the real implementation
const PlaneClientLive = Layer.effect(
  PlaneClient,
  Effect.gen(function* () {
    const config = yield* AppConfig       // can depend on other services
    return PlaneClient.of({
      getIssue: (id) => Effect.tryPromise({
        try: () => fetchIssue(config.apiUrl, id),
        catch: (e) => new PlaneError({ cause: e })
      }),
      patchIssue: (id, patch) => Effect.tryPromise({
        try: () => updateIssue(config.apiUrl, id, patch),
        catch: (e) => new PlaneError({ cause: e })
      })
    })
  })
)

// 4. Test implementation — swap in for tests, zero real I/O
const PlaneClientTest = Layer.succeed(PlaneClient, {
  getIssue: (id) => Effect.succeed({ id, state: "Prepare", title: "test" }),
  patchIssue: () => Effect.void
})
```

### Providing layers to run

```ts
const main = pipe(
  myProgram,                           // Effect<A, E, PlaneClient | JobStore | AppConfig>
  Effect.provide(PlaneClientLive),
  Effect.provide(JobStoreLive),
  Effect.provide(AppConfigLive),
)

Effect.runPromise(main)
```

**Layer composition:**

```ts
// Merge independent layers (no dependency between them)
const AppLayer = Layer.mergeAll(PlaneClientLive, JobStoreLive, AppConfigLive)

// provide(dependent, dependency) — when PlaneClientLive needs AppConfigLive to build
// argument order: DEPENDENT first, DEPENDENCY second
const AppLayer = Layer.provide(PlaneClientLive, AppConfigLive)
//                             ^^^^^^^^^^^^^^   ^^^^^^^^^^^^
//                             needs config     provides config

// Chain of dependencies: A needs B, B needs C
const Full = Layer.provide(
  Layer.provide(A_Live, B_Live),   // A depends on B
  C_Live                           // B depends on C
)
```

---

## @effect/sql + bun:sqlite

```ts
import { SqliteClient } from "@effect/sql-sqlite-bun"  // ← correct package name
import { SqlClient } from "@effect/sql"

// Layer setup
const SqliteLive = SqliteClient.layer({ filename: "./jobs.db" })

// Usage inside Effect.gen
const claimJob = (issueId: string, sessionKey: string) =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient
    yield* sql`
      INSERT INTO jobs (issue_id, session_key, claimed_at)
      VALUES (${issueId}, ${sessionKey}, ${Date.now()})
      ON CONFLICT (issue_id) DO UPDATE SET
        session_key = excluded.session_key,
        claimed_at  = excluded.claimed_at
    `
  })
// type: Effect<void, SqlError, SqlClient.SqlClient>

// Transactions
const atomicClaimAndUpdate = Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient
  yield* sql.withTransaction(
    Effect.gen(function* () {
      yield* sql`DELETE FROM jobs WHERE issue_id = ${id}`
      yield* sql`INSERT INTO audit_log ...`
    })
  )
})
```

---

## Config — Environment Variables

Never use `process.env` directly inside Effect programs. Use the `Config` module — it integrates with the Layer system and makes configuration testable.

```ts
import { Config, Effect, Layer } from "effect"

// Define typed config
const AppConfig = Config.all({
  planeApiKey: Config.string("PLANE_API_KEY"),
  planeUrl:    Config.string("PLANE_URL").pipe(Config.withDefault("https://plane.example.com")),
  dbPath:      Config.string("DB_PATH").pipe(Config.withDefault(":memory:")),
  dryRun:      Config.boolean("DRY_RUN").pipe(Config.withDefault(false)),
})

// Use in a Layer — config is resolved at Layer construction time
const PlaneClientLive = Layer.effect(
  PlaneClient,
  Effect.gen(function* () {
    const config = yield* AppConfig          // fails with ConfigError if missing
    return PlaneClient.of({ ... })
  })
)
```

**`Config` primitives:**

| Constructor | Type |
|-------------|------|
| `Config.string("KEY")` | `Config<string>` |
| `Config.number("KEY")` | `Config<number>` |
| `Config.boolean("KEY")` | `Config<boolean>` |
| `Config.withDefault(value)` | makes optional |
| `Config.all({ ... })` | struct of configs |
| `Config.map(f)` | transform the value |

`ConfigError` is automatically typed in the E channel when you `yield*` a `Config`.

---

## Schema — Validating External Data

Use `Schema` from `effect` to validate external API responses, database rows, and config at service boundaries. This is Effect's built-in replacement for Zod.

```ts
import { Schema } from "effect"

// Define a schema
const IssueSchema = Schema.Struct({
  id:    Schema.String,
  name:  Schema.String,
  state: Schema.String,
})
type Issue = Schema.Schema.Type<typeof IssueSchema>

// Validate an API response — decodes unknown → Issue or fails with ParseError
const decodeIssue = Schema.decodeUnknown(IssueSchema)

const getIssue = (id: string) =>
  Effect.gen(function* () {
    const raw = yield* Effect.tryPromise({
      try: () => fetch(`/api/issues/${id}`).then(r => r.json()),
      catch: (e) => new FetchError({ cause: e })
    })
    // Validate at the boundary — ParseError is tracked in E channel
    return yield* decodeIssue(raw)
  })
```

**Key Schema combinators:**

```ts
Schema.String                          // string
Schema.Number                          // number
Schema.Boolean                         // boolean
Schema.Literal("Prepare", "Test")      // union of string literals
Schema.Array(Schema.String)            // array
Schema.Struct({ id: Schema.String })   // object
Schema.Union(SchemaA, SchemaB)         // discriminated union
Schema.optional(Schema.String)         // optional field
```

---

## Match — Exhaustive Pattern Matching

Use `Match` for discriminated unions instead of if/else chains. It's exhaustive — TypeScript errors if you miss a case.

```ts
import { Match } from "effect"

type JobState =
  | { readonly _tag: "Prepare"; readonly issueId: string }
  | { readonly _tag: "Test";    readonly issueId: string; readonly sessionKey: string }
  | { readonly _tag: "Done";    readonly issueId: string }

// Match on _tag — exhaustive
const describeState = (state: JobState): string =>
  Match.value(state).pipe(
    Match.tag("Prepare", (s) => `${s.issueId} is being prepared`),
    Match.tag("Test",    (s) => `${s.issueId} testing in ${s.sessionKey}`),
    Match.tag("Done",    (s) => `${s.issueId} is complete`),
    Match.exhaustive    // compile error if any case is missing
  )

// Match returning an Effect — use Match.orElse or Match.exhaustive
const handleState = (state: JobState) =>
  Match.value(state).pipe(
    Match.tag("Prepare", (s) => spawnAgent(s.issueId)),
    Match.tag("Test",    (s) => runTests(s.sessionKey)),
    Match.tag("Done",    ()  => Effect.void),
    Match.exhaustive
  )
```

---

## Effect.tap / Effect.tapError

Use `tap` to observe values or perform side effects without changing them. Avoids unnecessary `gen` blocks just to log.

```ts
// tap — runs effect for side effect, passes original value through
const program = pipe(
  getIssue(issueId),
  Effect.tap(issue  => Effect.logInfo(`Found issue: ${issue.name}`)),
  Effect.tap(issue  => metrics.increment("issues.fetched")),
  Effect.flatMap(issue => processIssue(issue))
)

// tapError — runs on failure, passes error through unchanged
const safeProgram = pipe(
  riskyOperation(),
  Effect.tapError(err => Effect.logError("Operation failed", { err }))
)

// Both together
const robust = pipe(
  fetchData(),
  Effect.tap(d    => Effect.logDebug("Fetched", { count: d.length })),
  Effect.tapError(e => Effect.logError("Fetch failed", { cause: e })),
  Effect.map(transform)
)
```

---

## Error Handling Patterns

### Full recovery combinator menu

```ts
// catchTag — recover from one specific tagged error
Effect.catchTag("NotFoundError", (e) => Effect.succeed(defaultValue))

// catchTags — recover from multiple
Effect.catchTags({
  NotFoundError:  (e) => Effect.succeed(null),
  NetworkError:   (e) => Effect.fail(new ServiceUnavailable()),
})

// catchAll — recover from any expected error
Effect.catchAll((e) => Effect.logError("failed", e).pipe(Effect.as(null)))

// orElse — try a fallback effect on any failure
Effect.orElse(() => fetchFromFallback())

// orDie — convert expected error to defect (use sparingly)
Effect.orDie   // E channel becomes never; error becomes unrecoverable defect

// either — convert to Either, never fails (good for explicit branching)
const result: Either.Either<A, E> = yield* Effect.either(riskyEffect)
if (Either.isLeft(result)) { /* handle error */ }
```

### Catching specific expected errors

```ts
const safeGetIssue = (id: string) =>
  pipe(
    getIssue(id),
    Effect.catchTag("PlaneFetchError", (e) =>
      Effect.logError(`Plane API failed: ${e.statusCode}`).pipe(
        Effect.as(null)   // recover with null
      )
    )
  )
```

### Retries with backoff

```ts
import { Schedule } from "effect"

const withRetry = <A, E, R>(effect: Effect.Effect<A, E, R>) =>
  Effect.retry(
    effect,
    Schedule.exponential("100 millis").pipe(
      Schedule.compose(Schedule.recurs(3))
    )
  )
```

### Mapping errors

```ts
// Map one error type to another at a boundary
const fetchFromPlane = pipe(
  httpGet(url),
  Effect.mapError((e) => new PlaneError({ cause: e }))
)
```

---

## Option and Nullable Bridging

Use `Effect.fromNullable` and `Option` to bridge nullable JavaScript APIs without casting.

```ts
import { Effect, Option } from "effect"

// fromNullable — wraps null/undefined in NoSuchElementException
const user = yield* Effect.fromNullable(map.get(id))
// Fails with NoSuchElementException if undefined — typed in E channel

// Option inside Effect — when null is expected and not an error
const maybeUser = yield* Effect.sync(() => map.get(id)).pipe(
  Effect.map(Option.fromNullable)
)
// type: Effect<Option<User>, never, never>

// Pattern match on Option
Option.match(maybeUser, {
  onNone: () => "not found",
  onSome: (u) => u.name,
})

// getOrElse — provide a default
const name = Option.getOrElse(maybeUser, () => "anonymous")
```

---

## Resource Safety

Use `Effect.acquireRelease` / `Effect.scoped` for any resource that must be cleaned up:

```ts
const managedConnection = Effect.acquireRelease(
  openConnection(),           // acquire
  (conn) => conn.close()      // release — always runs, even on failure
)

const program = Effect.scoped(
  Effect.gen(function* () {
    const conn = yield* managedConnection
    return yield* conn.query("SELECT 1")
  })
)
```

---

## Concurrency

```ts
// Run effects in parallel, collect all results
const results = yield* Effect.all([fetchA(), fetchB(), fetchC()], {
  concurrency: "unbounded"
})

// Race — first to succeed wins, others cancelled
const result = yield* Effect.race(fetchFromPrimary(), fetchFromFallback())

// Bounded parallelism — max 3 at a time
yield* Effect.forEach(items, processItem, { concurrency: 3 })
```

---

## Observability — Logging and Tracing

Effect has built-in structured logging and OpenTelemetry tracing:

```ts
// Structured logging — use these, not console.log
yield* Effect.logInfo("Job claimed", { issueId, sessionKey })
yield* Effect.logError("Spawn failed", { issueId, cause: e })

// Spans for tracing
const program = Effect.withSpan("dispatch.claim", { attributes: { issueId } })(
  claimJob(issueId, sessionKey)
)
```

---

## Common Traps

### ❌ Using async/await inside Effect

```ts
// WRONG — async/await bypasses Effect's error channel and concurrency model
const getUser = (id: string) =>
  Effect.gen(function* () {
    const user = await fetchUser(id)   // ← never do this inside gen
    return user
  })

// RIGHT
const getUser = (id: string) =>
  Effect.gen(function* () {
    const user = yield* Effect.tryPromise({
      try: () => fetchUser(id),
      catch: (e) => new FetchError({ cause: e })
    })
    return user
  })
```

### ❌ Forgetting Effect.runPromise at the boundary

Effect values are descriptions. **Nothing runs** until you call `Effect.runPromise` (or `Effect.runSync`, `Effect.runFork`) at the program boundary — typically `main()`. Intermediate functions should return `Effect<...>`, not `Promise<...>`.

### ❌ Mixing Promise returns with Effect returns mid-stack

Pick one. If a function is Effect-based, its return type is `Effect<...>`. Wrapping an Effect function in a Promise (without `runPromise`) does nothing.

### ❌ Using `any` to escape type inference

If TypeScript can't infer the type, add an explicit annotation. Do not cast to `any`. Effect's type inference is load-bearing — `any` silently removes it.

### ❌ Forgetting `function*` in Effect.gen

```ts
// WRONG — arrow function, generator won't work
Effect.gen(() => { ... })

// RIGHT
Effect.gen(function* () { ... })
```

### ❌ Using `yield` instead of `yield*`

```ts
// WRONG
const user = yield getUser(id)

// RIGHT
const user = yield* getUser(id)
```

---

## Testing Effect Programs

Effect programs are highly testable because dependencies are injected via Layers. Use `bun:test` as the test runner.

```ts
import { describe, it, expect } from "bun:test"
import { Effect, Layer, Exit } from "effect"

// Test implementation — in-memory, no I/O
const JobStoreFake = Layer.succeed(JobStore, {
  claim:     (id, key) => Effect.void,
  getActive: ()        => Effect.succeed([]),
})

describe("dispatchJob", () => {
  it("claims the job and returns session", async () => {
    const program = dispatchJob("issue-1").pipe(
      Effect.provide(JobStoreFake),
      Effect.provide(PlaneClientFake),
    )

    const result = await Effect.runPromise(program)
    expect(result.sessionKey).toBe("expected-session")
  })

  it("fails with SpawnError when agent unavailable", async () => {
    const BrokenSpawner = Layer.succeed(AgentSpawner, {
      spawn: () => Effect.fail(new SpawnError({ reason: "unavailable" }))
    })

    const exit = await Effect.runPromiseExit(
      dispatchJob("issue-1").pipe(
        Effect.provide(JobStoreFake),
        Effect.provide(BrokenSpawner),
      )
    )

    // Check the failure type
    expect(Exit.isFailure(exit)).toBe(true)
    if (Exit.isFailure(exit)) {
      const err = exit.cause  // Cause<SpawnError>
      // use Cause.match or Cause.failureOption to inspect
    }
  })
})
```

**Testing rules:**
- Never use real I/O (network, filesystem) in unit tests — use `Layer.succeed` fakes
- Test the E channel explicitly — verify that errors are the right type, not just that the effect failed
- Use `Effect.runPromiseExit` (not `runPromise`) when you want to assert on failures without throwing
- For testing config: `Config.withDefault` or `ConfigProvider.fromMap` lets you supply test values

```ts
// Provide test config via ConfigProvider
const TestConfig = Layer.setConfigProvider(
  ConfigProvider.fromMap(new Map([
    ["PLANE_API_KEY", "test-key"],
    ["DB_PATH", ":memory:"],
  ]))
)

const program = myEffect.pipe(Effect.provide(TestConfig))
```

---

## Code-Review Checklist for Effect Code

When reviewing Effect-TS code, check for these anti-patterns:

### Type system integrity
- [ ] No `unknown` in the E channel — every expected error has a named tagged type
- [ ] No `any` casts anywhere in the Effect pipeline
- [ ] No `as` casts used to silence type errors
- [ ] Error union not collapsed to a base class or interface — it should be a discriminated union of tagged errors
- [ ] `R` channel (requirements) not incorrectly forced to `never` via `Effect.provide` in the wrong place

### Effect.gen correctness
- [ ] Uses `function*` not `() =>` (arrow function generators silently fail to type-check correctly)
- [ ] Uses `yield*` not `yield` on every effectful value
- [ ] No `await` inside `Effect.gen` — all async bridging via `Effect.tryPromise`
- [ ] No `console.log` — use `Effect.logInfo/logError/logDebug`

### Service / Layer patterns
- [ ] Dependencies declared in the `R` channel, not hardcoded with `new ServiceImpl()` inside gen
- [ ] `Layer.provide(dependent, dependency)` order is correct (dependent first)
- [ ] Test layers use `Layer.succeed` — not real I/O
- [ ] `Effect.runPromise` only at the outermost boundary, not inside helper functions

### SQL / resource safety
- [ ] Mutations inside `sql.withTransaction` when atomicity matters
- [ ] Row shapes typed explicitly in `sql<RowType>\`...\`` — not inferred as `unknown`
- [ ] snake_case → camelCase mapping is explicit (not assumed automatic)
- [ ] `Effect.acquireRelease` or `Effect.scoped` for any resource that needs cleanup

### Error handling
- [ ] `catchTag` / `catchTags` preferred over `catchAll` (more precise)
- [ ] `Effect.orDie` used deliberately and commented — not as a lazy escape hatch
- [ ] Retry schedules have a bound (`Schedule.recurs(n)`) — unbounded retries are a bug

---

## Patterns for the Plane Dispatcher Context

### Durable job state in SQLite

Model jobs as a tagged union; persist on every state transition:

```ts
type JobState =
  | { readonly _tag: "Prepare"; readonly issueId: string; readonly claimedAt: number }
  | { readonly _tag: "Test";    readonly issueId: string; readonly sessionKey: string; readonly startedAt: number }
  | { readonly _tag: "Review";  readonly issueId: string; readonly sessionKey: string; readonly startedAt: number }
  | { readonly _tag: "Done";    readonly issueId: string; readonly completedAt: number }

// Persist the full state JSON — rehydrate on startup
const persistState = (state: JobState) =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient
    yield* sql`
      INSERT INTO job_states (issue_id, state_tag, state_json, updated_at)
      VALUES (${state.issueId}, ${state._tag}, ${JSON.stringify(state)}, ${Date.now()})
      ON CONFLICT (issue_id) DO UPDATE SET
        state_tag  = excluded.state_tag,
        state_json = excluded.state_json,
        updated_at = excluded.updated_at
    `
  })
```

### Session liveness check

```ts
// Check if a spawned session is still alive by inspecting session history
class SessionDead extends Data.TaggedError("SessionDead")<{ sessionKey: string }> {}

const assertSessionAlive = (sessionKey: string) =>
  Effect.gen(function* () {
    const sessions = yield* SessionsService
    const history = yield* sessions.getHistory(sessionKey)
    if (history.messages.length === 0) {
      yield* Effect.fail(new SessionDead({ sessionKey }))
    }
    return history
  })
```

### Atomic claim (replaces in-flight.json write race)

```ts
const atomicClaim = (issueId: string, sessionKey: string) =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient
    yield* sql.withTransaction(
      sql`
        INSERT INTO jobs (issue_id, session_key, state, claimed_at)
        VALUES (${issueId}, ${sessionKey}, 'Prepare', ${Date.now()})
        ON CONFLICT (issue_id) DO NOTHING
      `
    )
  })
```

---

## Installation

```bash
bun add effect @effect/sql @effect/sql-sqlite-bun
# For AI features:
bun add @effect/ai @effect/ai-anthropic
```

**Row shape note:** `@effect/sql-sqlite-bun` returns column names verbatim (snake_case). Map to camelCase manually if your interface uses it:
```ts
const rows = yield* sql<{ session_key: string; claimed_at: number }>`SELECT ...`
return rows.map(r => ({ sessionKey: r.session_key, claimedAt: r.claimed_at }))
```

**Key packages:**

| Package | Purpose |
|---------|---------|
| `effect` | Core: Effect, Layer, Schedule, Stream, Fiber, etc. |
| `@effect/sql` | SQL abstraction (queries, transactions, migrations) |
| `@effect/sql-sqlite-bun` | bun:sqlite driver for @effect/sql |
| `@effect/ai` | Provider-agnostic LLM integration |
| `@effect/ai-anthropic` | Anthropic backend for @effect/ai |

---

## API Lookup — FTS5 Index

The skill ships a local SQLite FTS5 index over both the narrative docs and the full TypeDoc API reference. Use this when you need exact function signatures, combinator options, or type parameters that aren't in the SKILL.md examples.

### First-time setup (run once per machine)

```bash
cd ~/dev/openclaw-skill-effect-ts
python3 scripts/build-index.py
# Takes ~2 min — downloads llms-full.txt + crawls 230 API pages
# Re-run with --force to refresh after an Effect version update
```

The index is written to `scripts/effect-api.db` (gitignored — build locally).

### Searching

```bash
# Find function/combinator docs
python3 scripts/search-api.py "retry exponential backoff"
python3 scripts/search-api.py "Layer provide merge"
python3 scripts/search-api.py "Data TaggedError"

# Restrict to API reference only (signatures, type params)
python3 scripts/search-api.py "withTransaction" --source api-reference

# Restrict to a specific module
python3 scripts/search-api.py "exponential" --module "effect/Schedule"
python3 scripts/search-api.py "withTransaction" --module "sql/SqlClient"

# JSON output (for piping or programmatic use)
python3 scripts/search-api.py "Effect retry" --json --limit 5

# Increase result count
python3 scripts/search-api.py "stream error recovery" --limit 10
```

**Note on dotted names:** FTS5 treats `.` as a token boundary. `Effect.retry` is automatically split into `"Effect" "retry"` by the search script — both tokens are searched. Use plain word queries for best results: `retry exponential` rather than `Effect.retry Schedule.exponential`.

### What's indexed

| Source | Content |
|--------|---------|
| `llms-full.txt` | Full narrative docs: guides, concepts, code examples (764 chunks) |
| `effect` package | 175 API modules: Effect.ts, Schedule.ts, Layer.ts, Stream.ts, Schema.ts, Fiber.ts, … |
| `@effect/sql` | 13 modules: SqlClient.ts, Statement.ts, Migrator.ts, Model.ts, … |
| `@effect/ai` | 39 modules: LanguageModel.ts, Tool.ts, AnthropicLanguageModel.ts, … |

### When to use

- You need the exact signature of an obscure combinator (`Schedule.makeWithState`, `Stream.groupByKey`)
- You want all overloads of a function
- You're looking for which module exports a symbol
- Cross-referencing the narrative explanation with the TypeDoc signature

---

## References

- Full docs (LLM-optimised): https://effect.website/llms-full.txt
- Getting started: https://effect.website/docs/getting-started/introduction/
- Error management: https://effect.website/docs/error-management/two-error-types/
- Services: https://effect.website/docs/requirements-management/services/
- Generators: https://effect.website/docs/getting-started/using-generators/
- @effect/sql: https://effect.website/docs/sql/
- Effect AI: https://effect.website/docs/ai/introduction/
- API reference: https://effect-ts.github.io/effect/docs/effect
