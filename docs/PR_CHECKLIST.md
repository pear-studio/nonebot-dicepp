# PR Checklist

## Schema Change Checklist

- [ ] If database schema changed, a new migration version is added under `core/data/migrations/`
- [ ] Migration version is unique and strictly greater than existing versions
- [ ] Migration is idempotent (safe to run repeatedly)
- [ ] Related tests are added/updated (execution path + idempotency)
- [ ] `migrate check` passes for the target bot database
- [ ] Docs are updated if operational behavior/commands changed
