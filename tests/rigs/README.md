# DocVault Test Rigs

Standalone CLI diagnostic scripts. Each rig validates one layer of the
infrastructure migration. Exit 0 = all checks passed, 1 = failure.

## Usage

All rigs support `--help`, `--verbose`, and path overrides.

```bash
python tests/rigs/<rig>.py --help
python tests/rigs/<rig>.py --verbose
```

## Rigs

| Script | Layer | Gate | Requires server? |
|---|---|---|---|
| `check_db_migration.py` | 1 | DB schema, vaults, logs.db | No |
| `check_settings_resolution.py` | 2 | 4-tier settings chain | No |
| `check_resource_governor.py` | 2 | Sensor init, throttle state machine | No |
| `check_worker_priority.py` | 2 | Composite priority, aging | No |
| `check_vault_api.py` | 3 | Full vault CRUD + state machine | **Yes** |

## Run all offline rigs

```bash
python tests/rigs/check_db_migration.py && \
python tests/rigs/check_settings_resolution.py && \
python tests/rigs/check_resource_governor.py && \
python tests/rigs/check_worker_priority.py && \
echo "All offline checks passed"
```

## Run Layer 3 rig (server must be running)

```bash
python run.py &
python tests/rigs/check_vault_api.py --verbose
```
