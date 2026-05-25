# Collaborative assignments

Purohit can use `project_dir/control/assignments.yaml` to assign events to operators while allowing a shared monitor/control surface.

This is an application-level guard and audit aid. It does not replace filesystem permissions, Condor accounting permissions, or cluster account policy.

The assignment check runs inside the cluster-local manager, immediately before mutating commands are executed. This is important for the current architecture:

```text
central command center or tunnel UI
  -> appends a command to a cluster-local queue
  -> cluster-local tunnel/static manager drains the queue
  -> assignment check runs on that cluster-local manager
  -> bilby_pipe/condor command runs locally only if allowed
```

This is therefore compatible with:

```text
same-cluster jobs, e.g. source == target
remote-imported jobs with status.yaml:submit_ini
central multi-cluster command routing
```

## Protected actions

The following mutating actions are checked:

```text
submit_event
hold_event
release_event
remove_event
reset_event
```

Read-only monitor/status/file views are not restricted by this module.

## Example assignment file

Create:

```text
<project_dir>/control/assignments.yaml
```

Example:

```yaml
users:
  - vaishak.prasad
  - alice
  - bob

admins:
  - vaishak.prasad

policy:
  mode: hash

events:
  S240413p:
    assigned_to: alice
    reason: manual handoff
  S240514x:
    assigned_to: bob
    reason: debugging follow-up
```

## Supported policies

### Manual

```yaml
users:
  - vaishak.prasad
  - alice
policy:
  mode: manual

events:
  S240413p: vaishak.prasad
```

Unlisted events are unassigned and therefore unrestricted.

### Stable hash

```yaml
users:
  - vaishak.prasad
  - alice
  - bob
policy:
  mode: hash
```

`round_robin` and `stable_round_robin` are accepted aliases. The assignment is a stable hash of the event id modulo the number of users.

### Month based

```yaml
users:
  - vaishak.prasad
  - alice
  - bob
policy:
  mode: month
  month_owners:
    "01": vaishak.prasad
    "02": alice
    "03": bob
```

For event names like `S240413p`, month `04` is extracted from the event id. If no owner is configured for that month, Purohit falls back to `(month - 1) % n_users`.

## Operator identity

Commands may include an explicit operator:

```json
{
  "action": "submit_event",
  "event": "S240413p",
  "operator": "alice"
}
```

If no operator is provided, the manager falls back to the Unix user running the cluster-local manager process.

For a central command center, the central layer should include `operator` when it routes commands to a cluster queue. Without that, all commands appear as the cluster-local service account.

## Failure behavior

If an event is assigned to another non-admin operator, the command is rejected before submission or Condor mutation:

```json
{
  "ok": false,
  "forbidden": true,
  "operator": "bob",
  "assigned_to": "alice",
  "assignment_source": "manual"
}
```

The rejection is included in the command result and audit record.

## Same-cluster and remote-import compatibility

Assignments operate on event names and do not care how the event was prepared.

For a same-cluster workflow, the manager submits the original copied INI.

For a remote-import workflow, PR #40 records `submit_ini` in `status.yaml`; the manager still calls the same `submit_event` path and the same assignment guard applies before `find_event_config()` chooses the target-cluster submit INI.

## Shared group permissions

A typical shared directory setup is:

```bash
chgrp -R purohit-rean5 /path/to/shared/rean5
chmod -R g+rwX /path/to/shared/rean5
find /path/to/shared/rean5 -type d -exec chmod g+s {} \;
setfacl -R -m g:purohit-rean5:rwx /path/to/shared/rean5
setfacl -R -d -m g:purohit-rean5:rwx /path/to/shared/rean5
```

Run managers with:

```bash
umask 002
```
