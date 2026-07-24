# Database migrations

Production schema changes are owned by Alembic. Run migrations before starting
the API process:

```bash
uv run python -m cs2_manager.migrate upgrade
```

The first run safely adopts an existing pre-Alembic database under a MySQL
advisory lock. It executes the ordered legacy normalizers once, validates the
result, stamps the baseline, and then applies versioned revisions. An empty
database is created entirely by the versioned Alembic baseline.

Application startup must only call the revision check; it must not run
`SQLModel.metadata.create_all()` or the legacy migration functions.

If legacy credential rows exist, `CREDENTIAL_ENCRYPTION_KEYS`,
`CREDENTIAL_ACTIVE_KEY_ID`, and `TOKEN_HASH_KEY` (or `SECRET_KEY` as the
compatibility fallback) must be configured before upgrading. The data revision
precomputes all transformations and applies them in one transaction; failures
leave it unapplied and safe to retry.
