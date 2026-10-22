# Security and data handling

The suite records synthetic executions; it never invokes business functions. Real side effects need
your own authorization, validation, durable idempotency and recovery design.

Keys come from environment variables. Default reports omit request headers, response bodies and
arguments. `--save-traces` intentionally exports arguments. Treat external traces, adapters and
configurations as trusted inputs.

Use the repository's private vulnerability reporting feature when available. Otherwise open a minimal
issue asking for a private contact channel without exploit details, credentials or customer data.
No security contact address is configured yet.
