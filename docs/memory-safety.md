# Local process and memory checks

The terminal launcher passes its process ID to the proxy. If that owner exits,
the proxy waits at least 60 seconds without a connected client. It then checks
every loaded thread. Active, unknown, and failed status checks preserve the
backend. A confirmed idle backend can stop. A connected client can remain idle
without losing its session.

The launcher handles termination and terminal closure. Shutdown stops the backend
process group so its tool workers do not remain after the backend exits.

Run `jev-codex cleanup` to inspect abandoned proxies from earlier installations.
Run `jev-codex cleanup --apply` to stop eligible proxies. The command checks the
managed installation path, exited owner, terminal client, process start time,
and backend status. It repeats the checks before sending a normal termination
signal. It does not force termination of an old proxy whose shutdown fails.
Save its JSON output as the cleanup record.

If `host-memory-check` is on PATH, Jev uses it before a new terminal session,
worker, or turn starts. A nonzero exit blocks the new work. Interruptions and
existing turns can still finish. The host check is optional on other hosts;
install it as part of the Mac's local runtime setup. Existing proxy processes
keep the code from their original release until they exit.
