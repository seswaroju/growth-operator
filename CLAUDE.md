# Growth Operator — Claude Code Instructions

This file defines the permanent operating rules for Claude Code when working in this repository.

These instructions apply to every session, every ticket, and every file change.

---

## 1. Project objective

Build the Growth Operator jewelry MVP as a secure, modular, multi-tenant application.

The initial product must support this end-to-end workflow:

1. A jewelry business owner authenticates.
2. The owner creates or accesses an organization.
3. The organization installs or uses the jewelry vertical pack.
4. Jewelry catalog data is imported or created.
5. A customer inquiry enters through a conversation channel.
6. The system generates a catalog-grounded AI response draft.
7. An authorized human reviews, edits, approves, or rejects the draft.
8. Only an approved action may be sent or executed.
9. The action is recorded in the audit log.
10. Lead outcome and attributable revenue can be recorded.
11. The owner can see measurable business value.

The immediate goal is not to build every future capability.

The immediate goal is to create a reliable MVP for the first jewelry pilot customers.

---

## 2. Current MVP boundaries

Build only the currently approved jewelry MVP unless the founder explicitly expands scope.

### In scope

- Authentication
- OTP verification
- Sessions and token rotation
- Organizations
- Tenant membership
- RBAC
- Tenant isolation
- Audit logging
- Events and outbox
- WhatsApp-oriented conversation architecture
- Jewelry vertical pack loading
- Catalog
- Pricing
- Customers and leads
- Conversations and messages
- AI draft generation
- Human approvals
- Workflows required by the jewelry MVP
- Campaigns required by the MVP
- ROI and attribution
- Owner-facing frontend
- Tests, observability, and deployment required for pilot use

### Out of scope unless explicitly approved

- Production Kirana functionality
- Additional production verticals
- Plugin marketplace
- Capability marketplace
- Public developer marketplace
- Kubernetes
- Multi-region deployment
- Complex microservices
- Enterprise SSO
- Advanced billing
- Full ERP replacement
- Fully autonomous customer-facing actions
- Large-scale data warehouse
- Advanced multi-touch attribution
- Premature infrastructure abstraction
- Features not required for the first jewelry customers

Kirana may remain as a modularity proof and declarative vertical-pack example, but it must not become active MVP scope without founder approval.

---

## 3. Before doing anything else

At the beginning of every task:

1. Read `project-management/CURRENT_TASK.md`.
2. Confirm that it identifies exactly one active ticket.
3. Read `project-management/BLOCKERS.md`.
4. Read `project-management/DECISIONS.md`.
5. Read the active ticket under:

   ```text
   docs/tickets/MVP-NNN.md
   ```

6. Read every authoritative specification linked by that ticket.
7. Inspect the current implementation.
8. Inspect the current tests.
9. Check the current Git branch and working tree.
10. Do not modify files until the mandatory task protocol is completed and the founder approves the plan.

Do not implement from memory, general convention, or assumptions when an authoritative project document exists.

---

## 4. Documentation source of truth

`docs/` is a symbolic link to:

```text
../Growth-Operator-Vault
```

The Obsidian vault is the source of truth for:

- Product requirements
- Architecture
- Platform specifications
- Vertical-pack specifications
- MVP tickets
- API specifications
- Database plans
- Agent specifications
- Implementation starter-kit documents

### Documentation rules

- Treat `docs/` as read-only from this repository.
- Never modify files through the `docs/` symlink.
- Never silently change implementation away from the authoritative documentation.
- When documentation and existing code conflict:
  1. stop,
  2. identify the conflict,
  3. explain the consequences,
  4. propose alternatives,
  5. wait for founder approval.
- Approved architectural or product changes must be recorded in:

  ```text
  project-management/DECISIONS.md
  ```

- Temporary defects and environment issues belong in:

  ```text
  project-management/BLOCKERS.md
  ```

- Do not put temporary implementation status inside this file.

---

## 5. Mandatory task protocol

Before modifying any file, provide a plan containing all of the following.

### 5.1 Task identification

- Active ticket
- Ticket title
- Current Git branch
- Objective
- Authoritative documents reviewed

### 5.2 Scope

- In-scope work
- Out-of-scope work
- Assumptions
- Open questions
- Blockers

### 5.3 Expected changes

List:

- Files expected to be created
- Files expected to be modified
- Files expected to be deleted, if any
- Database migrations
- Database tables or indexes
- APIs or routes
- Events
- Background jobs
- Frontend pages or components
- Tests
- Dependencies
- Configuration changes
- Commands expected to run

### 5.4 Risk review

Identify relevant risks involving:

- Security
- Privacy
- Secrets
- Tenant isolation
- RLS
- Authentication
- Authorization
- Data migration
- Backward compatibility
- Idempotency
- Concurrency
- External side effects
- API compatibility
- Event compatibility
- Data loss
- Rollback

### 5.5 Approval gate

After presenting the plan:

- Wait for explicit founder approval.
- Do not begin editing until approval is given.
- Do not interpret requests for analysis, review, audit, explanation, or planning as permission to edit files.

---

## 6. Scope and change control

- Work only on the active ticket.
- Do not implement adjacent tickets unless explicitly approved.
- Do not broaden MVP scope.
- Do not perform unrelated refactoring.
- Do not rewrite working modules without a ticket requirement.
- Do not create abstractions solely for hypothetical future use.
- Do not add unused frameworks or services.
- Do not change the approved architecture silently.
- Do not change framework choices without founder approval.
- Do not change the database migration sequence without founder approval.
- Do not invent table names when the authoritative migration plan defines them.
- Do not change public API contracts without approval.
- Do not change event contracts without approval.
- Do not invent new canonical error codes without an approved decision.
- Do not implement production functionality for another vertical unless explicitly approved.

When a requested implementation requires work outside the active ticket:

1. report the dependency,
2. explain why it is required,
3. propose whether it should be included or handled separately,
4. wait for approval.

---

## 7. Git workflow and safety

### 7.1 Branch rules

- Never implement directly on `main`.
- Use one feature branch per ticket.
- Branch format:

  ```text
  feature/mvp-NNN-short-description
  ```

Example:

```text
feature/mvp-011-otp-auth
```

Before implementation, run and report:

```bash
git status --short
git branch --show-current
git log --oneline -5
```

If the current branch is `main`, stop and request permission to create the ticket branch.

### 7.2 Git actions requiring explicit approval

Never perform any of the following unless explicitly instructed:

- `git commit`
- `git push`
- `git merge`
- `git rebase`
- `git reset`
- `git cherry-pick`
- `git clean`
- Force push
- Branch deletion
- Tag creation or deletion
- History rewriting

### 7.3 Working-tree protection

- Never discard uncommitted work.
- Never overwrite unrelated user changes.
- Never assume an untracked file is safe to delete.
- Never use `git reset --hard`.
- Never use `git clean -fd`.
- Never stage or commit secrets.
- Never commit through automation unless specifically instructed.

After implementation, report:

```bash
git status --short
git diff --stat
git diff --check
```

Also list:

- Every changed file
- Every created file
- Every deleted file
- Every untracked file

The founder reviews and stages changes.

---

## 8. Command safety rules

Never run destructive commands without explicit founder approval.

Destructive commands include, but are not limited to:

```bash
rm -rf
git reset --hard
git clean -fd
docker compose down -v
dropdb
DROP DATABASE
DROP SCHEMA
DROP TABLE
terraform destroy
kubectl delete
```

Also treat the following as destructive:

- Deleting Docker volumes
- Deleting queues
- Purging event streams
- Clearing production caches
- Deleting cloud resources
- Deleting storage buckets
- Rewriting migration history
- Removing populated database columns
- Destroying local development data that has not been backed up

Before requesting approval for a destructive action, explain:

1. What will be deleted
2. Why it is necessary
3. Whether recovery is possible
4. What backup exists
5. What rollback exists
6. Whether a non-destructive alternative exists

Prefer inspection and dry-run commands first.

---

## 9. Dependency rules

Do not add, remove, or upgrade a dependency unless:

1. The approved ticket or authoritative specification requires it.
2. The dependency is included in the implementation plan.
3. The reason for using it is explained.
4. The founder approves the plan.

For every proposed dependency, report:

- Package name
- Proposed version or version range
- Purpose
- License concerns, if material
- Security implications
- Operational impact
- Whether the same requirement can be implemented without it

Never add a dependency merely to avoid writing a small amount of straightforward code.

Material dependency decisions must be recorded in:

```text
project-management/DECISIONS.md
```

---

## 10. Security and privacy rules

### 10.1 Secrets

Never print, expose, commit, or persist:

- API keys
- Access tokens
- Refresh tokens
- OTPs
- JWT signing secrets
- Encryption keys
- Passwords
- Database credentials
- Cloud credentials
- Webhook signing secrets
- WhatsApp credentials
- Payment credentials

Never place secrets in:

- Source code
- Tests
- Fixtures
- Markdown
- Screenshots
- Git history
- Audit records
- Normal application logs
- Example payloads

Use fake or clearly invalid values in examples and automated tests.

### 10.2 Customer and business data

Treat the following as sensitive:

- Phone numbers
- Email addresses
- Customer conversations
- Addresses
- Purchase history
- Jewelry preferences
- Wedding details
- Business metrics
- Revenue
- Pricing and margins
- Supplier information
- Customer images
- Catalog images that are not public
- Payment details
- Authentication data

Logs must redact:

- Tokens
- OTPs
- Credentials
- Sensitive message content
- Personal data that is not required for diagnosis

### 10.3 OTP development behavior

Plaintext OTPs must never appear in:

- API responses
- Persistent logs
- Audit logs
- Database fields
- Test snapshots
- Error responses
- Production output

A local-development OTP delivery adapter may display an OTP only when all of the following are true:

1. An explicit development-only flag is enabled.
2. The application environment is verified as local development.
3. The flag is disabled by default.
4. Production startup fails if the flag is enabled.
5. The OTP is not persisted.
6. The OTP is not returned from an API.
7. The OTP is not written to normal application logs.

### 10.4 External side effects

Never perform a real external action without explicit founder approval.

Examples:

- Sending an SMS
- Sending a WhatsApp message
- Sending an email
- Publishing an advertisement
- Posting to social media
- Creating a payment request
- Charging a customer
- Changing a Google Business Profile
- Updating production catalog data
- Calling a real customer
- Sending a review request
- Mutating a production system

Use fake, sandbox, mock, or simulated adapters until explicitly approved.

### 10.5 Production systems

Never:

- Connect to production
- Run migrations against production
- Modify production configuration
- Delete production data
- Send production messages
- Use production credentials

unless the founder explicitly approves the exact action.

---

## 11. Architecture

### 11.1 Architectural style

The MVP is a modular FastAPI monolith, not a microservices system.

Use clear module boundaries inside one deployable backend.

Do not split modules into independent services unless an approved architecture decision requires it.

### 11.2 Four-layer separation

| Layer | Owns | Location | Changed by |
|---|---|---|---|
| L0 Platform-invariant | Runtime, events, approvals, audit, channels, tenancy, mediation | `core/` | Platform implementation |
| L1 Vertical pack | Bindings, catalog schema, pricing strategy, workflows, prompts, compliance | `verticals/<name>/` | Pack configuration |
| L2 Tenant settings | Profile, policies, credentials, configuration, slot values | Database | Owner or authorized user |
| L3 Runtime state | Conversations, leads, memory, runs, approvals, events | Database | Normal system operation |

Billing is deferred until its approved ticket.

### 11.3 Rule zero

`core/` must never contain industry-specific nouns or logic.

Examples of forbidden industry-specific concepts in `core/`:

- gold
- karat
- jewelry
- necklace
- ring
- diamond
- menu item
- restaurant table
- appointment slot
- medical specialty

`core/` must never import from:

```text
verticals/
```

Vertical packs must be loaded through platform interfaces at runtime.

Vertical packs should contain declarative configuration such as:

- YAML
- JSON
- Markdown
- Catalog schemas
- Pricing strategies
- Workflow definitions
- Prompt layers
- Evaluation suites
- UI templates
- Integration configuration

### 11.4 Authoritative architecture documents

Read before modifying platform boundaries:

```text
docs/21-platform/core-platform.md
docs/21-platform/vertical-adapter-layer.md
```

---

## 12. Core module map

Implementation status changes continuously.

Never infer implementation completeness from:

- This file
- Directory existence
- Module names
- Prior Claude responses
- The point-in-time audit alone

Before modifying a module:

1. Inspect the current source.
2. Inspect its current tests.
3. Read `project-management/CURRENT_TASK.md`.
4. Read linked specifications.
5. Treat `project-management/IMPLEMENTATION_AUDIT.md` as a historical snapshot.
6. Verify status using Git and executable commands.

Current intended module ownership:

```text
core/api/
    FastAPI application, routers, request/response handling

core/runtime/
    Agent execution, planning, checkpoints, model routing

core/mediation/
    Tool proxy, permission enforcement, sandbox adapters

core/approvals/
    Approval policy engine, approvals, execution tokens

core/workflows/
    Workflow DSL, workflow execution, guards, waits, resume

core/prompts/
    Prompt registry, composition, versions, evaluation integration

core/catalog/
    Catalog storage, validation, history, retrieval, search

core/pricing/
    Pricing strategies, rate sources, quote calculation, ledger

core/packs/
    Vertical-pack loading, validation, installation, registry

core/channels/
    Channel interfaces and adapters

core/channels/whatsapp/
    WhatsApp-specific adapter implementation

core/tenancy/
    Authentication, sessions, organizations, RBAC, RLS context, settings

core/ingestion/
    Extract, normalize, validate, review, and load stages

core/audit/
    Append-only audit records, hash-chain integrity, anchoring

core/common/
    Configuration, errors, shared platform primitives

core/events/
    Transactional outbox, consumers, deduplication, retries, DLQ

core/insights/
    Digests, business metrics, ROI, attribution summaries

core/worker.py
    Worker process entry point

core/scheduler.py
    Scheduled-job entry point
```

Each module's `__init__.py` may reference authoritative documents. Read those documents before adding logic.

---

## 13. Error handling

The canonical error taxonomy is defined in:

```text
core/common/errors.py
```

Current canonical codes include:

```text
stale_rate
unledgered_figure
approval_required
permission_denied_manifest
pack_conflict
config_schema_violation
suppressed_contact
consent_missing
tenant_paused
budget_exceeded
checkpoint_conflict
provider_unavailable
```

Use:

```python
GrowthOperatorError(code=..., detail=...)
```

The FastAPI exception handler converts platform errors to RFC 7807:

```text
application/problem+json
```

Rules:

- Do not invent a new canonical error code without founder approval.
- Do not leak stack traces, credentials, tokens, or sensitive information.
- Use stable error codes for programmatic handling.
- Add tests for error responses.
- Preserve error compatibility when changing APIs.

---

## 14. Configuration

The canonical settings implementation is:

```text
core/common/config.py
```

Configuration precedence:

1. Explicit initialization arguments
2. Process environment
3. `.env`
4. SOPS-decrypted secrets file
5. Defaults

Environment variables must use:

```text
GROWTH_OPERATOR_
```

Examples:

```text
GROWTH_OPERATOR_DATABASE_URL
GROWTH_OPERATOR_REDIS_URL
```

Temporary configuration defects belong in:

```text
project-management/BLOCKERS.md
```

Do not document temporary environment bugs in this permanent file.

---

## 15. Database and migration rules

### 15.1 Migration framework

The project uses:

- PostgreSQL
- Alembic
- Async database engine
- Row-level security for organization-owned data

Migration configuration is located in:

```text
alembic.ini
migrations/env.py
migrations/script.py.mako
migrations/lib/rls.py
migrations/versions/
```

### 15.2 Authoritative migration order

Follow:

```text
docs/25-implementation-starter-kit/09-database-migration-order.md
```

Do not invent or reorder migrations without approval.

### 15.3 Row-level security

Every organization-scoped table must enable RLS in the same migration that creates it.

Use:

```python
apply_rls(table_name)
```

and:

```python
drop_rls(table_name)
```

from:

```text
migrations/lib/rls.py
```

Tenant context must use transaction-local configuration:

```sql
SET LOCAL app.org_id = ...
```

Never use session-level tenant context.

No tenant context must fail closed.

Never disable or bypass RLS to make a test pass.

### 15.4 Migration safety

- Never edit a migration already applied outside a disposable local environment.
- Every migration must have a deterministic revision ID.
- Every migration must have the correct dependency.
- Never silently rewrite migration history.
- Never drop populated tables or columns without approval.
- Never rename populated columns without approval.
- Never change populated-column types without approval.
- Never run migrations against production without approval.
- Never use destructive migration shortcuts solely to simplify local development.

Migration work must include, where applicable:

- Upgrade verification
- Downgrade verification
- Schema inspection
- Constraint verification
- Index verification
- RLS verification
- Cross-tenant isolation tests
- Rollback considerations

---

## 16. API rules

- Follow the authoritative API specification.
- Use versioned routes under `/v1/`.
- Validate every request.
- Use typed request and response models.
- Do not expose internal database models directly.
- Preserve stable response shapes.
- Use RFC 7807 for platform errors.
- Apply authentication and authorization explicitly.
- Apply tenant context before organization-owned queries.
- Do not trust tenant IDs supplied by clients.
- Do not leak the existence of another tenant's resources.
- Add tests for:
  - success,
  - validation failure,
  - unauthenticated access,
  - unauthorized access,
  - cross-tenant access,
  - not found,
  - conflict,
  - idempotency where applicable.

---

## 17. Authentication and authorization rules

- Passwordless OTP is the approved MVP authentication path.
- OTPs must be hashed at rest.
- OTPs must expire.
- Verification attempts must be limited.
- Resends must be throttled.
- Sessions must be revocable.
- Refresh tokens must rotate.
- Token reuse must be detectable where specified.
- JWT claims must follow the authoritative specification.
- Authorization must be enforced server-side.
- Frontend visibility is not authorization.
- Deny access by default.
- Every organization-owned request must have verified tenant context.
- Cross-tenant access must be tested explicitly.

---

## 18. AI and agent rules

- AI output is untrusted until validated.
- Never allow a model response to bypass business rules.
- Never allow a model to bypass permissions.
- Never allow a model to bypass approvals.
- Never let a model invent:
  - products,
  - prices,
  - availability,
  - discounts,
  - customer history,
  - business policy.
- Ground customer-facing drafts in approved business data.
- Record:
  - prompt version,
  - model,
  - model configuration,
  - evidence used,
  - warnings,
  - confidence where defined.
- Use deterministic fake providers in unit tests.
- Do not call paid or production model providers in automated tests.
- Every external action must pass through the mediation and approval boundaries defined by the architecture.
- All safety-relevant agent actions must be auditable.

---

## 19. Human approval rules

Important customer-facing, financial, operational, and external actions must follow the approved human-in-the-loop policy.

Claude must not implement a shortcut that sends or executes an action before approval.

Approval-sensitive actions include:

- Customer replies
- Campaign sends
- Discounts
- Price commitments
- Payment links
- Inventory actions
- Supplier communication
- Public posts
- Advertising actions

Approval workflows must support:

- Draft creation
- Pending approval
- Edit
- Approve
- Reject
- Rejection reason
- Expiration where required
- Idempotent execution
- Immutable history
- Audit linkage

---

## 20. Events and idempotency

Event-producing changes should use the approved transactional outbox pattern.

Rules:

- Business mutation and outbox write must be atomic where specified.
- Consumers must be idempotent.
- Duplicate events must not duplicate side effects.
- External requests must use idempotency keys where supported.
- Retries must be bounded.
- Poison events must move to a DLQ or equivalent failure state.
- Event payloads must be versioned.
- Never put secrets or unnecessary personal data into events.
- Add tests for:
  - duplicate delivery,
  - retry,
  - ordering assumptions,
  - failure handling,
  - idempotency.

---

## 21. Testing requirements

Tests must be written with the implementation, not deferred.

The repository uses:

```text
tests/unit/
tests/integration/
tests/contract/
tests/isolation/
tests/e2e/
```

### 21.1 Unit tests

Cover:

- Business rules
- Validation
- Pure functions
- Error conditions
- Security boundaries
- Idempotency logic

### 21.2 Integration tests

Cover:

- Database behavior
- Migrations
- API to database flow
- Redis behavior
- Outbox behavior
- External adapter boundaries using fakes or sandboxes

### 21.3 Isolation tests

Explicitly verify:

- Organization A cannot read Organization B data.
- Organization A cannot update Organization B data.
- Organization A cannot infer Organization B resource existence.
- Missing tenant context fails closed.
- Background workers apply tenant context correctly.

### 21.4 Contract tests

Verify:

- API contracts
- Event contracts
- Adapter contracts
- Vertical-pack contracts

### 21.5 End-to-end tests

Verify approved user journeys across:

- Frontend
- API
- Database
- Agent runtime
- Approvals
- Audit
- Simulated outbound actions
- ROI tracking

### 21.6 Test quality

- Do not create tests that merely mirror the implementation.
- Test observable behavior.
- Include negative and boundary cases.
- Include security-sensitive failure paths.
- Never remove or weaken a test to make the suite pass without approval.
- Never mark tests skipped without reporting why.
- Report warnings and expected failures.
- A passing suite does not override an unmet acceptance criterion.

---

## 22. Commands

### 22.1 Python

The project uses `uv` and Python 3.12.

```bash
uv sync
uv run ruff check .
uv run ruff check --fix .
uv run mypy core
uv run mypy migrations --exclude 'migrations/versions'
uv run pytest
uv run pytest -v
uv run pytest tests/unit/test_scaffold.py::test_every_core_module_imports_clean
```

Create a migration using the ticket-approved migration name:

```bash
uv run alembic revision -m "<ticket-approved-migration-name>"
```

Migration commands:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic history
uv run alembic current
```

### 22.2 Makefile

```bash
make dev
make migrate
make test
make seed
```

`make down` may remove Docker volumes depending on the Makefile implementation.

Do not run destructive volume-removal behavior without explicit approval.

### 22.3 Frontend

```bash
cd web
npm install
npm run dev
npm run lint
npx tsc -b --noEmit
npm run build
```

### 22.4 Git inspection

```bash
git status --short
git branch --show-current
git diff --stat
git diff
git diff --check
git log --oneline -5
```

---

## 23. Command execution reporting

After implementation, report every command run.

Include:

- Command
- Exit status
- Result
- Failures
- Warnings
- Skipped checks
- Checks that could not be run
- Reason a check could not be run

Never claim a command passed unless it was actually executed successfully.

Distinguish clearly between:

- Executed and passed
- Executed and failed
- Inspected statically
- Not executed
- Blocked by environment
- Deferred with founder approval

---

## 24. Project tracking

The repository contains:

```text
project-management/CURRENT_TASK.md
project-management/IMPLEMENTATION_LOG.md
project-management/DECISIONS.md
project-management/BLOCKERS.md
project-management/IMPLEMENTATION_AUDIT.md
```

### 24.1 Current task

`CURRENT_TASK.md` must describe exactly one active ticket.

After implementation:

- Do not replace it with another ticket automatically.
- Mark the current ticket:

  ```text
  Completed — awaiting founder review
  ```

- Wait for the founder to explicitly select the next ticket.

### 24.2 Implementation log

`IMPLEMENTATION_LOG.md` is append-only.

For each completed ticket, append:

- Date
- Ticket
- Approved plan
- Files changed
- Migrations
- APIs
- Events
- Frontend changes
- Commands run
- Test results
- Known issues
- Commit hash, after a commit exists
- Next recommended action

Never edit or delete prior entries.

### 24.3 Decisions

Update `DECISIONS.md` only when the founder explicitly approves a product or technical decision.

Never treat an open question as an approved decision.

### 24.4 Blockers

Update `BLOCKERS.md` whenever:

- Work cannot be verified
- A dependency is unavailable
- An external account is pending
- A founder decision is required
- An environment defect remains
- A security or architecture issue remains

Do not delete resolved blockers.

Move them to a resolved section with:

- Resolution date
- Resolution
- Commit or evidence

### 24.5 Implementation audit

`IMPLEMENTATION_AUDIT.md` is a point-in-time snapshot.

Do not assume it reflects the current repository after significant changes.

Verify current status directly from:

- Source code
- Tests
- Git
- Executable commands

---

## 25. Completion report

After implementing an approved ticket, stop modifying files and provide a final report.

The report must include:

### 25.1 Scope report

- Ticket
- Objective
- In-scope work completed
- Out-of-scope work avoided
- Assumptions made

### 25.2 Changed files

For every changed file:

- Path
- Change type
- Reason

### 25.3 Database report

- Migrations created
- Tables changed
- Indexes changed
- Constraints changed
- RLS status
- Upgrade result
- Downgrade result
- Remaining migration risk

### 25.4 API report

- Routes added or changed
- Request models
- Response models
- Authentication requirements
- Authorization requirements
- Error responses

### 25.5 Test report

- Tests added
- Tests changed
- Commands run
- Passed
- Failed
- Skipped
- Warnings
- Unverified behavior

### 25.6 Security report

- Authentication impact
- Authorization impact
- Tenant-isolation impact
- Secret-handling impact
- Logging impact
- External-side-effect impact

### 25.7 Remaining work

- Known issues
- Deferred work
- Blockers
- Founder decisions required

Do not commit until explicitly instructed.

---

## 26. Requirement-to-evidence matrix

Every completed ticket must include a matrix in the final report.

Use this format:

| Acceptance criterion | Implementation file(s) | Test name(s) | Verification command | Result |
|---|---|---|---|---|
| Criterion text | File paths | Test identifiers | Exact command | PASS / FAIL / BLOCKED |

Rules:

- Every acceptance criterion must have a row.
- Do not mark a criterion PASS based only on code inspection when execution is required.
- Use `BLOCKED` when the environment prevents verification.
- Explain every blocked criterion.
- A ticket is not complete when a critical criterion remains unverified unless the founder explicitly accepts the risk.

---

## 27. Definition of done

A ticket is not complete until all applicable requirements below are satisfied.

### Scope

- Approved scope is implemented.
- No unapproved adjacent ticket was implemented.
- No unrelated refactoring was introduced.
- No prohibited premature feature was added.

### Requirements

- Every acceptance criterion is mapped to evidence.
- Every required test case exists.
- Every unresolved requirement is disclosed.

### Code quality

- Ruff passes.
- Mypy passes.
- Frontend lint passes when frontend code changes.
- Frontend type-check passes when frontend code changes.
- Frontend build passes when frontend code changes.
- No dead or unreachable code was knowingly introduced.
- No unnecessary dependency was added.
- No unrelated file was changed.

### Database

- Migrations are verified when migrations change.
- Upgrade is verified.
- Downgrade is verified where supported.
- Organization-owned tables have RLS.
- Tenant isolation is tested.
- Destructive migration risk is disclosed.

### Security

- Security-sensitive failure paths are tested.
- No plaintext secret was introduced.
- No sensitive customer data was placed in logs or fixtures.
- Authorization is enforced server-side.
- Cross-tenant access is tested where applicable.
- External side effects remain gated.

### Testing

- Required tests are added.
- Relevant existing tests still pass.
- Failed commands are disclosed.
- Skipped tests are disclosed.
- Warnings are disclosed.
- Unverified behavior is disclosed.

### Tracking

- `project-management/IMPLEMENTATION_LOG.md` is appended.
- `project-management/BLOCKERS.md` is updated when needed.
- `project-management/DECISIONS.md` is updated only for approved decisions.
- `project-management/CURRENT_TASK.md` is marked:

  ```text
  Completed — awaiting founder review
  ```

### Review

- Claude presents the final changed-file report.
- Claude presents the requirement-to-evidence matrix.
- The founder reviews the diff.
- The founder explicitly approves any commit.
- The founder explicitly selects the next ticket.

---

## 28. Prohibited behavior

Claude must never:

- Implement directly on `main`
- Commit without permission
- Push without permission
- Merge without permission
- Rewrite Git history
- Delete unrelated files
- Discard uncommitted changes
- Modify the documentation vault
- Choose the next ticket automatically
- Expand scope without approval
- Introduce a framework without approval
- Add a dependency without approval
- Disable a test to obtain a passing result
- Disable RLS
- Bypass authorization
- Bypass human approval
- Send real customer communications without approval
- Run destructive commands without approval
- Claim tests passed when they were not run
- Claim a ticket is complete when critical acceptance criteria remain unverified
- Store secrets or plaintext OTPs
- Connect to production without approval
- Run production migrations without approval
- Invent business rules absent from the authoritative specifications

---

## 29. Founder authority

The founder is the final decision-maker for:

- Product scope
- Ticket selection
- Architecture changes
- Framework changes
- Dependency changes
- Database sequence changes
- API contract changes
- External integrations
- Production access
- Real customer communication
- Destructive operations
- Commits
- Pushes
- Releases
- Deployment
- Risk acceptance

When uncertain, stop and ask.

Do not silently resolve a material ambiguity.