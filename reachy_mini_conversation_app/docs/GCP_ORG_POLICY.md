# GCP Org Policy — Allowing Service Account Key Creation

The `losiern-vibe` project is governed by an organization policy that blocks service account key creation by default. This documents the steps taken to override it at the project level so the Reachy Mini service account key can be generated.

## Context

Two org policy constraints block SA key creation:

| Constraint | Notes |
|---|---|
| `constraints/iam.disableServiceAccountKeyCreation` | Legacy constraint |
| `constraints/iam.managed.disableServiceAccountKeyCreation` | Newer managed constraint (supersedes legacy) |

Both must be overridden at the project level. The override does not affect other projects in the org.

## Steps Taken

### 1. Enable the Organization Policy API

```bash
gcloud services enable orgpolicy.googleapis.com --project=losiern-vibe
```

### 2. Override legacy constraint

```bash
cat > /tmp/sa-key-policy.yaml << 'EOF'
name: projects/losiern-vibe/policies/iam.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: false
EOF
gcloud org-policies set-policy /tmp/sa-key-policy.yaml --project=losiern-vibe
```

### 3. Override managed constraint

```bash
cat > /tmp/sa-key-policy-managed.yaml << 'EOF'
name: projects/losiern-vibe/policies/iam.managed.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: false
EOF
gcloud org-policies set-policy /tmp/sa-key-policy-managed.yaml --project=losiern-vibe
```

### 4. Wait for propagation (~60 seconds) then verify

```bash
gcloud org-policies describe constraints/iam.disableServiceAccountKeyCreation \
  --project=losiern-vibe
# Should show: enforce: false
```

## Notes

- Policy changes take ~30–90 seconds to propagate before key creation succeeds
- These overrides are project-scoped — org-level enforcement on other projects is unchanged
- Account used: `admin@example.com` (requires `orgpolicy.policy.set` permission)
- If you need to re-apply after a project reset, re-run steps 2–3
