# Security Policy

This tool's whole promise is "nothing leaves your machine except LLM calls to providers you chose." Two classes of bug break that promise and are treated as security issues:

1. **False green**: the audit exits 0 while the harness tree contains egress the baseline never covered (a missed snapshot surface, a scan gap, a parser blind spot).
2. **Lockdown gap**: a reviewed `lockdowns/<release>.yaml` misses an egress channel present in that upstream release.

Report either privately via [GitHub security advisories](../../security/advisories/new) rather than a public issue, including the harness release, the audit output, and the file/line of the missed egress. Everything else — crashes, bad diffs, false *reds* — is a normal public issue.

The audit is a tripwire, not a proof: it diffs compositions, endpoint strings, and wire-extension names. Reports that upstream restructured in a way that moves those signals are exactly what we want to hear about.
